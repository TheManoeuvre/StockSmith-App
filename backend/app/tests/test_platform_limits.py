"""The constraint matrix: strictest-wins resolution, and each check function.

Everything here runs without a database on purpose — the resolver and the checkers are
pure, and that is the property worth protecting. If a future change makes one of these
need a session, it will fail to import here first.
"""

from app.models.listing import ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.platform_limits import (
    LimitField,
    Severity,
    check_charset,
    check_count,
    check_length,
    check_product,
    default_limits,
    resolve_effective_limits,
    supported_platforms,
)

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


def test_single_platform_resolves_to_its_own_limits():
    effective = resolve_effective_limits({EBAY})
    assert effective[LimitField.sku_max_length].value == 50
    assert effective[LimitField.sku_max_length].platform is EBAY


def test_strictest_limit_wins_across_platforms():
    effective = resolve_effective_limits({ETSY, EBAY})
    # Etsy is stricter on SKU (32 vs 50) and on variation attributes (2 vs 5)...
    assert effective[LimitField.sku_max_length].value == 32
    assert effective[LimitField.variation_attribute_max_count].value == 2
    # ...but eBay is stricter on title (80 vs 140). "Strictest" is per field, not per
    # platform — a single "most restrictive platform" would get one of these wrong.
    assert effective[LimitField.title_max_length].value == 80


def test_resolution_reports_which_platform_imposed_each_limit():
    """Every user-facing message names the culprit, so losing this attribution turns an
    actionable error into a confusing one."""
    effective = resolve_effective_limits({ETSY, EBAY})
    assert effective[LimitField.sku_max_length].platform is ETSY
    assert effective[LimitField.title_max_length].platform is EBAY


def test_no_platforms_means_no_limits():
    assert resolve_effective_limits(set()) == {}


def test_shopify_has_no_limits_and_imposes_none():
    """Shopify is in the platform enum but has no adapter. Giving it limits would let it
    silently constrain products it can never be listed on."""
    assert ListingPlatform.shopify not in supported_platforms()
    assert default_limits(ListingPlatform.shopify) == {}
    assert resolve_effective_limits({ETSY, ListingPlatform.shopify})[
        LimitField.sku_max_length
    ].value == 32


def test_sku_at_the_limit_passes_and_one_over_fails():
    effective = resolve_effective_limits({ETSY})
    assert check_length(LimitField.sku_max_length, "X" * 32, effective) is None
    violation = check_length(LimitField.sku_max_length, "X" * 33, effective)
    assert violation is not None
    assert violation.severity is Severity.blocker
    assert violation.current_length == 33
    # Names the platform, and states the limit once.
    assert violation.message == "SKU is 33 characters, over Etsy's limit of 32."


def test_live_catalogue_sku_length_is_accepted():
    """The longest SKU on the live shop is exactly 32 and is active on Etsy, which is the
    empirical basis for the default. A change that makes this fail has moved the number
    below something already proven to work."""
    effective = resolve_effective_limits({ETSY, EBAY})
    assert check_length(LimitField.sku_max_length, "SKU-0037-6-STUD-SUNFLOWER-YELLOW", effective) is None


def test_absent_value_is_not_a_conformance_problem():
    """Missing data is a completeness question, answered by draft-readiness. Reporting it
    here too would double-count it in the compatibility report."""
    effective = resolve_effective_limits({ETSY})
    assert check_length(LimitField.sku_max_length, None, effective) is None
    assert check_length(LimitField.sku_max_length, "", effective) is None


def test_over_length_warning_suggests_a_truncation_but_a_blocker_does_not():
    effective = resolve_effective_limits({EBAY})
    title = check_length(LimitField.title_max_length, "word " * 30, effective)
    assert title is not None and title.severity is Severity.warning
    assert title.suggested_value is not None and len(title.suggested_value) <= 80
    # No trailing ellipsis: it reads as a rendering bug on a live listing.
    assert not title.suggested_value.endswith("…")

    sku = check_length(LimitField.sku_max_length, "X" * 99, effective)
    assert sku is not None and sku.severity is Severity.blocker
    # A blocker deliberately offers no mechanical fix — shortening a SKU has to go
    # through the live-SKU protection, not a blind truncate.
    assert sku.suggested_value is None


def test_truncation_prefers_a_word_boundary_but_not_a_wasteful_one():
    effective = resolve_effective_limits({EBAY})
    spaced = check_length(LimitField.title_max_length, "alpha beta " * 12, effective)
    assert spaced is not None and not spaced.suggested_value.endswith(" ")
    assert " " in spaced.suggested_value

    # One long token with a space near the very start: honouring that boundary would
    # strand most of the budget, so it hard-cuts instead.
    value = "ab " + "Z" * 200
    long_token = check_length(LimitField.title_max_length, value, effective)
    assert long_token is not None and len(long_token.suggested_value) == 80


def test_etsy_rejects_parentheses_in_attribute_values():
    """Confirmed in Etsy's own schema for ListingInventoryProduct.property_values."""
    violation = check_charset(LimitField.attribute_value_charset, "Large (boxed)", {ETSY})
    assert violation is not None
    assert violation.imposed_by is ETSY
    assert violation.suggested_value == "Large boxed"


def test_ebay_alone_allows_parentheses():
    assert check_charset(LimitField.attribute_value_charset, "Large (boxed)", {EBAY}) is None


def test_charset_rules_apply_from_every_platform_not_just_the_strictest():
    """Charsets don't reduce to one winner — a character forbidden anywhere is unusable
    on a value pushed everywhere."""
    assert check_charset(LimitField.attribute_value_charset, "Large (boxed)", {ETSY, EBAY}) is not None


def test_etsy_allows_ampersand_once_but_not_twice():
    assert check_charset(LimitField.title_charset, "Nuts & Bolts", {ETSY}) is None
    violation = check_charset(LimitField.title_charset, "Nuts & Bolts & Screws", {ETSY})
    assert violation is not None
    assert "only once" in violation.message


def test_count_check_reports_the_overage():
    effective = resolve_effective_limits({ETSY})
    assert check_count(LimitField.variation_max_count, 100, "variations", effective) is None
    violation = check_count(LimitField.variation_max_count, 101, "variations", effective)
    assert violation is not None and violation.severity is Severity.blocker


def _product(**kwargs) -> Product:
    defaults = dict(
        id=1,
        name="Brick Pencil Pot",
        sku="SKU-0037",
        description="A pot.",
        current_stock=0,
        allocated_qty=0,
        is_active=True,
    )
    defaults.update(kwargs)
    return Product(**defaults)


def _variant(**kwargs) -> ProductVariant:
    defaults = dict(
        id=1,
        product_id=1,
        variant_name="4 Stud / Blue",
        sku_suffix="4-STUD-BLUE",
        current_stock=0,
        allocated_qty=0,
        is_active=True,
    )
    defaults.update(kwargs)
    return ProductVariant(**defaults)


def test_product_with_no_variants_is_checked_as_a_single_unit():
    """Mirrors listing_sync._unit_checks — a product with no active variants is itself
    the unit. If these two ever disagree the report would describe something the sync
    check never looks at."""
    effective = resolve_effective_limits({ETSY})
    _, units = check_product(_product(), [], effective, {ETSY})
    assert len(units) == 1
    assert units[0].variant_id is None
    assert units[0].sku == "SKU-0037"


def test_variant_units_use_the_composed_full_sku():
    effective = resolve_effective_limits({ETSY})
    _, units = check_product(_product(), [_variant()], effective, {ETSY})
    assert units[0].sku == "SKU-0037-4-STUD-BLUE"


def test_third_attribute_breaches_etsy_but_not_ebay():
    product = _product(
        variant_attribute1_name="Size",
        variant_attribute2_name="Colour",
        variant_attribute3_name="Finish",
    )
    etsy_violations, _ = check_product(product, [], resolve_effective_limits({ETSY}), {ETSY})
    fields = [v.field for v in etsy_violations]
    assert LimitField.variation_attribute_max_count in fields
    assert next(v for v in etsy_violations if v.field == LimitField.variation_attribute_max_count).severity is (
        Severity.blocker
    )

    ebay_violations, _ = check_product(product, [], resolve_effective_limits({EBAY}), {EBAY})
    assert LimitField.variation_attribute_max_count not in [v.field for v in ebay_violations]


def test_two_attributes_are_clean_on_both_platforms():
    """The live catalogue's worst case. This should stay clean, and if it stops being so
    the default limits have drifted away from data known to work."""
    product = _product(variant_attribute1_name="Studs", variant_attribute2_name="Colour")
    violations, units = check_product(
        product,
        [_variant(attribute1_value="6 Stud", attribute2_value="Sunflower Yellow")],
        resolve_effective_limits({ETSY, EBAY}),
        {ETSY, EBAY},
    )
    assert violations == []
    assert units[0].violations == []


def test_too_many_images_is_a_warning_not_a_blocker():
    effective = resolve_effective_limits({ETSY})
    violations, _ = check_product(_product(), [], effective, {ETSY}, image_count=21)
    image = next(v for v in violations if v.field == LimitField.image_max_count)
    assert image.severity is Severity.warning


def test_stock_over_etsy_quantity_cap_is_flagged_on_the_unit():
    effective = resolve_effective_limits({ETSY})
    _, units = check_product(_product(), [_variant(current_stock=1500)], effective, {ETSY})
    assert LimitField.quantity_max in [v.field for v in units[0].violations]


def test_em_dash_and_accents_are_accepted_in_a_title():
    r"""Etsy's rule is expressed in Unicode categories: an em dash is \p{Pd} (punctuation)
    and an accented letter is \p{L}, so both are valid. An ASCII-approximated character
    class rejects them and fills the report with false positives on good titles."""
    assert check_charset(LimitField.title_charset, "Brick \u2014 Caf\u00e9 Pot", {ETSY}) is None


def test_emoji_is_rejected_from_a_title():
    violation = check_charset(LimitField.title_charset, "Brick Pot \U0001f389", {ETSY})
    assert violation is not None
    assert violation.suggested_value == "Brick Pot"


def test_trademark_symbols_are_explicitly_allowed():
    assert check_charset(LimitField.title_charset, "Brick Pot\u2122 \u00a9 \u00ae", {ETSY}) is None
