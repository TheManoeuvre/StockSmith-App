"""Pure coverage for propose_variation_mapping's match_confidence truth table — no
session/DB needed since Product/ProductVariant are only read from here, never
persisted, matching test_payment_state_parsing.py's style.
"""

from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.listing_adoption import propose_variation_mapping
from app.services.platforms.base import ClassicListingCandidate


def _candidate(skus: list[str], specifics: list[dict[str, str]] | None) -> ClassicListingCandidate:
    return ClassicListingCandidate(
        external_listing_id="227269664481",
        title="Aqara G400 mount",
        listing_type="FixedPriceItem",
        skus=skus,
        variation_specifics=specifics,
        quantity=4,
        is_migrated=False,
    )


def test_no_variant_product_matches_sole_sku():
    product = Product(id=1, name="Widget", sku="WIDGET")
    candidate = _candidate(["WIDGET-SOLE"], None)

    proposal = propose_variation_mapping(product, [], candidate)

    assert len(proposal.entries) == 1
    entry = proposal.entries[0]
    assert entry.variant_id is None
    assert entry.matched_sku == "WIDGET-SOLE"
    assert entry.match_confidence == "exact"


def test_no_variant_product_multiple_skus_is_unmatched():
    product = Product(id=1, name="Widget", sku="WIDGET")
    candidate = _candidate(["WIDGET-A", "WIDGET-B"], None)

    proposal = propose_variation_mapping(product, [], candidate)

    assert proposal.entries[0].match_confidence == "unmatched"
    assert proposal.entries[0].matched_sku is None


def test_exact_attribute_match_across_variants():
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    candidate = _candidate(
        ["SKU-0012-A", "SKU-0012-B"],
        [{"Colour": "Black"}, {"Colour": "White"}],
    )

    proposal = propose_variation_mapping(product, variants, candidate)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert by_variant[10].matched_sku == "SKU-0012-A"
    assert by_variant[10].match_confidence == "exact"
    assert by_variant[11].matched_sku == "SKU-0012-B"
    assert by_variant[11].match_confidence == "exact"


def test_exact_match_is_case_insensitive():
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [ProductVariant(id=10, product_id=1, variant_name="black", attribute1_value="black")]
    candidate = _candidate(["SKU-0012-A"], [{"colour": "BLACK"}])

    proposal = propose_variation_mapping(product, variants, candidate)

    assert proposal.entries[0].match_confidence == "exact"
    assert proposal.entries[0].matched_sku == "SKU-0012-A"


def test_mismatched_variant_count_is_unmatched_for_every_entry():
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
        ProductVariant(id=12, product_id=1, variant_name="Red", attribute1_value="Red"),
    ]
    candidate = _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}])

    proposal = propose_variation_mapping(product, variants, candidate)

    assert all(e.match_confidence == "unmatched" for e in proposal.entries)
    assert all(e.matched_sku is None for e in proposal.entries)


def test_count_matches_but_attributes_dont_line_up_falls_back_to_positional():
    """No StockSmith attribute values set at all — same count, nothing to match against
    by value, so it proposes a positional pairing rather than guessing 'exact'."""
    product = Product(id=1, name="Mount", sku="SKU-0012")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Variant A"),
        ProductVariant(id=11, product_id=1, variant_name="Variant B"),
    ]
    candidate = _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}])

    proposal = propose_variation_mapping(product, variants, candidate)

    assert all(e.match_confidence == "count_only" for e in proposal.entries)
    matched_skus = {e.matched_sku for e in proposal.entries}
    assert matched_skus == {"SKU-0012-A", "SKU-0012-B"}


def test_partial_attribute_match_leaves_remainder_count_only():
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="Mystery"),  # no attribute value set
    ]
    candidate = _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}])

    proposal = propose_variation_mapping(product, variants, candidate)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert by_variant[10].match_confidence == "exact"
    assert by_variant[10].matched_sku == "SKU-0012-A"
    assert by_variant[11].match_confidence == "count_only"
    assert by_variant[11].matched_sku == "SKU-0012-B"
