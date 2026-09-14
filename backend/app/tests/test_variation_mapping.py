"""Pure coverage for propose_variation_mapping's match_confidence truth table — no
session/DB needed since Product/ProductVariant are only read from here, never
persisted, matching test_payment_state_parsing.py's style.
"""

import pytest

from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.listing_adoption import (
    UnknownPlatformAttribute,
    normalise_token,
    propose_etsy_variation_mapping,
    propose_variation_mapping,
)
from app.services.platforms.base import ClassicListingCandidate, ListingProductRef


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


def test_mismatched_variant_count_keeps_exact_matches_but_never_guesses_positionally():
    """Three variants, two variations: the ones that match by value are still pre-filled
    (they are exact, count or no count); only the odd one out is left for the user,
    with no positional fallback to lean on."""
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
        ProductVariant(id=12, product_id=1, variant_name="Red", attribute1_value="Red"),
    ]
    candidate = _candidate(["SKU-0012-A", "SKU-0012-B"], [{"Colour": "Black"}, {"Colour": "White"}])

    proposal = propose_variation_mapping(product, variants, candidate)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert (by_variant[10].matched_sku, by_variant[10].match_confidence) == ("SKU-0012-A", "exact")
    assert (by_variant[11].matched_sku, by_variant[11].match_confidence) == ("SKU-0012-B", "exact")
    assert (by_variant[12].matched_sku, by_variant[12].match_confidence) == (None, "unmatched")


def test_mismatched_count_with_nothing_to_match_on_is_unmatched_for_every_entry():
    product = Product(id=1, name="Mount", sku="SKU-0012")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="A"),
        ProductVariant(id=11, product_id=1, variant_name="B"),
        ProductVariant(id=12, product_id=1, variant_name="C"),
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


# --- Normalisation and attribute-name pairing ---------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Red / Blue", "redblue"),
        ("red-blue", "redblue"),
        ("RED  BLUE!", "redblue"),
        ("Größe", "grösse"),
        ("Size: 12mm (approx.)", "size12mmapprox"),
    ],
)
def test_normalise_token_drops_case_and_symbols(raw, expected):
    assert normalise_token(raw) == expected


def test_values_match_ignoring_case_and_symbols():
    product = Product(id=1, name="Mount", sku="SKU-0012", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Red / Blue", attribute1_value="Red / Blue"),
        ProductVariant(id=11, product_id=1, variant_name="Matt-Black", attribute1_value="Matt-Black"),
    ]
    candidate = _candidate(["A", "B"], [{"Colour": "matt black"}, {"Colour": "RED-BLUE"}])

    proposal = propose_variation_mapping(product, variants, candidate)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert (by_variant[10].matched_sku, by_variant[10].match_confidence) == ("B", "exact")
    assert (by_variant[11].matched_sku, by_variant[11].match_confidence) == ("A", "exact")


def test_attribute_names_pair_across_colour_spelling_and_punctuation():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour", variant_attribute2_name="Size")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black S", attribute1_value="Black", attribute2_value="S"),
        ProductVariant(id=11, product_id=1, variant_name="Black M", attribute1_value="Black", attribute2_value="M"),
    ]
    candidate = _candidate(
        ["A", "B"],
        [{"Color": "Black", "Size:": "M"}, {"Color": "Black", "Size:": "S"}],
    )

    proposal = propose_variation_mapping(product, variants, candidate)

    assert [(p.stocksmith_name, p.platform_name, p.source) for p in proposal.attribute_pairs] == [
        ("Colour", "Color", "exact"),
        ("Size", "Size:", "exact"),
    ]
    assert proposal.platform_attribute_names == ["Color", "Size:"]
    by_variant = {e.variant_id: e for e in proposal.entries}
    assert by_variant[10].matched_sku == "B"
    assert by_variant[11].matched_sku == "A"


def test_attribute_name_is_inferred_from_overlapping_values():
    """"Colour" vs "Primary colour": the names don't line up, but the values plainly do,
    so the pairing is inferred and flagged as such rather than left to the user."""
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    candidate = _candidate(
        ["A", "B"],
        [{"Primary colour": "White", "Finish": "Matt"}, {"Primary colour": "Black", "Finish": "Matt"}],
    )

    proposal = propose_variation_mapping(product, variants, candidate)

    assert [(p.platform_name, p.source) for p in proposal.attribute_pairs] == [("Primary colour", "inferred")]
    by_variant = {e.variant_id: e for e in proposal.entries}
    assert (by_variant[10].matched_sku, by_variant[10].match_confidence) == ("B", "exact")
    assert (by_variant[11].matched_sku, by_variant[11].match_confidence) == ("A", "exact")


def test_inference_declines_a_tie_and_leaves_the_attribute_unpaired():
    """Both platform attributes overlap our values equally — picking either would be a
    coin toss, so neither is picked and the rows fall back to positional review."""
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    candidate = _candidate(
        ["A", "B"],
        [{"Primary": "Black", "Secondary": "White"}, {"Primary": "White", "Secondary": "Black"}],
    )

    proposal = propose_variation_mapping(product, variants, candidate)

    assert [(p.platform_name, p.source) for p in proposal.attribute_pairs] == [(None, "unmatched")]
    assert all(e.match_confidence == "count_only" for e in proposal.entries)


def test_manual_attribute_map_overrides_automatic_pairing():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    candidate = _candidate(
        ["A", "B"],
        [{"Primary": "Black", "Secondary": "White"}, {"Primary": "White", "Secondary": "Black"}],
    )

    proposal = propose_variation_mapping(product, variants, candidate, {"Colour": "Secondary"})

    assert [(p.platform_name, p.source) for p in proposal.attribute_pairs] == [("Secondary", "manual")]
    by_variant = {e.variant_id: e for e in proposal.entries}
    assert (by_variant[10].matched_sku, by_variant[10].match_confidence) == ("B", "exact")
    assert (by_variant[11].matched_sku, by_variant[11].match_confidence) == ("A", "exact")


def test_manual_attribute_map_can_unpair_an_attribute():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    candidate = _candidate(["A", "B"], [{"Colour": "White"}, {"Colour": "Black"}])

    proposal = propose_variation_mapping(product, variants, candidate, {"Colour": None})

    assert [(p.platform_name, p.source) for p in proposal.attribute_pairs] == [(None, "unmatched")]
    # Nothing left to match by value — positional fallback, not the value-correct pairing.
    assert [(e.variant_id, e.matched_sku, e.match_confidence) for e in proposal.entries] == [
        (10, "A", "count_only"),
        (11, "B", "count_only"),
    ]


def test_manual_attribute_map_naming_an_unknown_platform_attribute_raises():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black")]
    candidate = _candidate(["A"], [{"Colour": "Black"}])

    with pytest.raises(UnknownPlatformAttribute):
        propose_variation_mapping(product, variants, candidate, {"Colour": "Shade"})


def test_ambiguous_variant_resolves_once_another_claims_the_alternative():
    """Product has Colour+Size but the listing only varies by Colour for one of them:
    "Black" fits two variations until "Black / L" claims one, then the other is the
    only fit left — the matcher iterates rather than giving up on first sight."""
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour", variant_attribute2_name="Size")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="Black L", attribute1_value="Black", attribute2_value="L"),
    ]
    candidate = _candidate(["A", "B"], [{"Colour": "Black", "Size": "L"}, {"Colour": "Black", "Size": "One size"}])

    proposal = propose_variation_mapping(product, variants, candidate)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert (by_variant[11].matched_sku, by_variant[11].match_confidence) == ("A", "exact")
    assert (by_variant[10].matched_sku, by_variant[10].match_confidence) == ("B", "exact")


def test_genuinely_ambiguous_variants_are_not_guessed_as_exact():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black S", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="Black M", attribute1_value="Black"),
    ]
    candidate = _candidate(["A", "B"], [{"Colour": "Black", "Size": "S"}, {"Colour": "Black", "Size": "M"}])

    proposal = propose_variation_mapping(product, variants, candidate)

    assert all(e.match_confidence == "count_only" for e in proposal.entries)


def test_listing_without_specifics_has_no_attribute_pairs():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black")]
    candidate = _candidate(["A"], None)

    proposal = propose_variation_mapping(product, variants, candidate)

    assert proposal.attribute_pairs == []
    assert proposal.platform_attribute_names == []
    assert proposal.entries[0].match_confidence == "count_only"
    assert proposal.entries[0].matched_variation_specifics is None


# --- Etsy wrapper -------------------------------------------------------------------


def _etsy_product(index: int, attributes: dict[str, str], sku: str | None = None) -> ListingProductRef:
    variation = ", ".join(f"{k}: {v}" for k, v in attributes.items()) or None
    return ListingProductRef(index=index, sku=sku, variation=variation, quantity=1, attributes=attributes)


def test_etsy_proposal_is_keyed_by_product_index():
    product = Product(id=1, name="Mount", sku="S", variant_attribute1_name="Colour")
    variants = [
        ProductVariant(id=10, product_id=1, variant_name="Black", attribute1_value="Black"),
        ProductVariant(id=11, product_id=1, variant_name="White", attribute1_value="White"),
    ]
    # Index 1 was a deleted product — indexes are positions in Etsy's array, not dense.
    products = [_etsy_product(0, {"Colour": "White"}), _etsy_product(2, {"Colour": "Black"}, sku="OLD")]

    proposal = propose_etsy_variation_mapping(product, variants, products)
    by_variant = {e.variant_id: e for e in proposal.entries}

    assert by_variant[10].matched_index == 2
    assert by_variant[10].matched_variation == "Colour: Black"
    assert by_variant[10].matched_attributes == {"Colour": "Black"}
    assert by_variant[10].match_confidence == "exact"
    assert by_variant[11].matched_index == 0
    assert proposal.attribute_pairs[0].platform_name == "Colour"


def test_etsy_no_variant_product_matches_sole_listing_product():
    product = Product(id=1, name="Widget", sku="WIDGET")

    proposal = propose_etsy_variation_mapping(product, [], [_etsy_product(0, {})])

    assert proposal.entries[0].variant_id is None
    assert proposal.entries[0].matched_index == 0
    assert proposal.entries[0].match_confidence == "exact"
