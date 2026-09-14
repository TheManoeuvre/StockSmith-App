"""Pure-function coverage for the unmigrated-listing eligibility pre-filter and the
Trading API GetMyeBaySelling response parsing — plain dicts/XML strings in, no session,
no network, matching test_payment_state_parsing.py's style.
"""

from xml.etree import ElementTree

import pytest

from app.services.platforms.ebay import _TRADING_NS, EbayAdapter, _evaluate_eligibility


@pytest.mark.parametrize(
    "skus,expected_missing_sku",
    [
        ([], True),
        ([""], True),
        (["SKU-1"], False),
        (["", ""], True),
    ],
)
def test_missing_sku_flagged(skus, expected_missing_sku):
    reasons = _evaluate_eligibility("FixedPriceItem", skus, None, detail_loaded=True)
    has_missing_sku_reason = any("no SKU" in r for r in reasons)
    assert has_missing_sku_reason is expected_missing_sku


@pytest.mark.parametrize("skus", [[], [""], ["", ""]])
def test_missing_sku_never_flagged_without_detail(skus):
    reasons = _evaluate_eligibility("FixedPriceItem", skus, None, detail_loaded=False)
    assert not any("no SKU" in r for r in reasons)


@pytest.mark.parametrize("listing_type", ["Chinese", "Classified"])
def test_ineligible_listing_type_flagged(listing_type):
    reasons = _evaluate_eligibility(listing_type, ["SKU-1"], None)
    assert any("not supported" in r for r in reasons)


def test_fixed_price_listing_type_is_fine():
    reasons = _evaluate_eligibility("FixedPriceItem", ["SKU-1"], None)
    assert reasons == []


def test_partial_variation_sku_coverage_flagged():
    reasons = _evaluate_eligibility(
        "FixedPriceItem",
        ["SKU-1", "", "SKU-3"],
        [{"Color": "Red"}, {"Color": "Blue"}, {"Color": "Green"}],
        listing_sku="PARENT",
    )
    assert any("variation(s) have no SKU" in r for r in reasons)


def test_full_variation_sku_coverage_is_fine():
    reasons = _evaluate_eligibility(
        "FixedPriceItem", ["SKU-1", "SKU-2"], [{"Color": "Red"}, {"Color": "Blue"}], listing_sku="PARENT"
    )
    assert reasons == []


def test_multi_variation_missing_listing_level_sku_flagged():
    """Every variation has a SKU but the listing itself doesn't — eBay's migration still
    rejects it ("The listing SKU cannot be null or empty"), so the picker must say why."""
    reasons = _evaluate_eligibility(
        "FixedPriceItem", ["SKU-1", "SKU-2"], [{"Color": "Red"}, {"Color": "Blue"}], listing_sku=None
    )
    assert any("listing itself has no SKU" in r for r in reasons)


def test_single_sku_listing_not_flagged_for_missing_listing_level_sku():
    """The listing-level-SKU reason is multi-variation only — a single-SKU listing's
    Item.SKU is already covered by the generic 'no SKU set' check."""
    reasons = _evaluate_eligibility("FixedPriceItem", ["SKU-1"], None, listing_sku="SKU-1")
    assert reasons == []


def test_multiple_reasons_can_combine():
    reasons = _evaluate_eligibility("Classified", [], None)
    assert len(reasons) == 2


_SAMPLE_ITEM_XML = """
<Item xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>227269664481</ItemID>
  <Title>Aqara G400 mount</Title>
  <ListingType>FixedPriceItem</ListingType>
  <SKU>SKU-0012</SKU>
  <SellingStatus><QuantityAvailable>7</QuantityAvailable></SellingStatus>
  <Variations>
    <Variation>
      <SKU>SKU-0012-A</SKU>
      <VariationSpecifics>
        <NameValueList><Name>Colour</Name><Value>Black</Value></NameValueList>
      </VariationSpecifics>
    </Variation>
    <Variation>
      <SKU>SKU-0012-B</SKU>
      <VariationSpecifics>
        <NameValueList><Name>Colour</Name><Value>White</Value></NameValueList>
      </VariationSpecifics>
    </Variation>
  </Variations>
</Item>
"""


def test_parse_classic_listing_multi_variation():
    element = ElementTree.fromstring(_SAMPLE_ITEM_XML)
    candidate = EbayAdapter._parse_classic_listing(element)

    assert candidate.external_listing_id == "227269664481"
    assert candidate.title == "Aqara G400 mount"
    assert candidate.listing_type == "FixedPriceItem"
    assert candidate.quantity == 7
    assert candidate.skus == ["SKU-0012-A", "SKU-0012-B"]
    assert candidate.listing_sku == "SKU-0012"
    assert candidate.variation_specifics == [{"Colour": "Black"}, {"Colour": "White"}]
    assert candidate.ineligibility_reasons == []


_SAMPLE_SINGLE_SKU_ITEM_XML = """
<Item xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>111222333</ItemID>
  <Title>Single SKU widget</Title>
  <ListingType>FixedPriceItem</ListingType>
  <SKU>WIDGET-1</SKU>
  <SellingStatus><QuantityAvailable>3</QuantityAvailable></SellingStatus>
</Item>
"""


def test_parse_classic_listing_single_sku():
    element = ElementTree.fromstring(_SAMPLE_SINGLE_SKU_ITEM_XML)
    candidate = EbayAdapter._parse_classic_listing(element)

    assert candidate.skus == ["WIDGET-1"]
    assert candidate.listing_sku == "WIDGET-1"
    assert candidate.variation_specifics is None
    assert candidate.quantity == 3


def test_parse_multi_variation_without_listing_level_sku_flags_it():
    """The real-world case behind this: a 4-variation listing where every Variation.SKU is
    set but the listing itself has no Custom Label. Parses fine, but is flagged ineligible
    until an Item.SKU is added."""
    xml = _SAMPLE_ITEM_XML.replace("<SKU>SKU-0012</SKU>", "")
    candidate = EbayAdapter._parse_classic_listing(ElementTree.fromstring(xml), detail_loaded=True)

    assert candidate.skus == ["SKU-0012-A", "SKU-0012-B"]
    assert candidate.listing_sku is None
    assert any("listing itself has no SKU" in r for r in candidate.ineligibility_reasons)


_SAMPLE_NO_SKU_ITEM_XML = """
<Item xmlns="urn:ebay:apis:eBLBaseComponents">
  <ItemID>444555666</ItemID>
  <Title>Legacy listing, no SKU set</Title>
  <ListingType>FixedPriceItem</ListingType>
  <SellingStatus><QuantityAvailable>1</QuantityAvailable></SellingStatus>
</Item>
"""


def test_list_view_never_claims_missing_sku():
    """The regression this guards: GetMyeBaySelling's ActiveList doesn't reliably return
    the Variations block, so an empty `skus` from the list view means "we don't know",
    not "no SKU set". Asserting ineligibility from it would grey out, in the picker,
    exactly the multi-variation listings this feature exists to adopt."""
    element = ElementTree.fromstring(_SAMPLE_NO_SKU_ITEM_XML)
    candidate = EbayAdapter._parse_classic_listing(element)  # detail_loaded=False by default

    assert candidate.skus == []
    assert candidate.detail_loaded is False
    assert candidate.ineligibility_reasons == []


def test_detail_view_does_flag_missing_sku():
    """Once GetItem has authoritatively said there's no SKU, it IS a real ineligibility."""
    element = ElementTree.fromstring(_SAMPLE_NO_SKU_ITEM_XML)
    candidate = EbayAdapter._parse_classic_listing(element, detail_loaded=True)

    assert candidate.detail_loaded is True
    assert any("no SKU" in r for r in candidate.ineligibility_reasons)


def test_ineligible_listing_type_is_flagged_even_without_detail():
    """Listing type comes back reliably in the list view, so unlike SKUs it can be
    judged without per-item detail."""
    element = ElementTree.fromstring(_SAMPLE_NO_SKU_ITEM_XML.replace("FixedPriceItem", "Classified"))
    candidate = EbayAdapter._parse_classic_listing(element)

    assert any("not supported" in r for r in candidate.ineligibility_reasons)
    assert not any("no SKU" in r for r in candidate.ineligibility_reasons)


def test_trading_ns_matches_sample_xml_namespace():
    """Guards against the namespace map drifting out of sync with what eBay's Trading
    API actually returns — every find()/findall() above silently matches nothing if
    this string is wrong, which is exactly the kind of failure this pure test exists to
    catch without needing a live account."""
    assert _TRADING_NS["e"] == "urn:ebay:apis:eBLBaseComponents"
