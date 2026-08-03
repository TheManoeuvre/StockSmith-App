"""Coverage for the SKU-alignment path — the part of this feature that edits live
marketplace listings, so the payload it builds gets tested directly rather than only
through the flow that calls it.

The specific hazard: eBay treats a variation omitted from a ReviseFixedPriceItem
<Variations> block as one to DELETE. A builder that dropped or misaligned a variation
would therefore destroy real inventory (and its sales history) on a production shop,
and would do it silently — the call would succeed.
"""

from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.listing_adoption import plan_sku_alignment
from app.services.platforms.base import ClassicListingCandidate
from app.services.platforms.ebay import (
    _TRADING_SITE_IDS,
    EbayAdapter,
    _align_new_skus,
    _is_already_migrated_error,
    _raise_for_trading_ack,
    _xml_escape,
)
from app.services.platforms.errors import PlatformSyncError

_NS = {"e": "urn:ebay:apis:eBLBaseComponents"}


def _candidate(skus, specifics, is_migrated=False):
    return ClassicListingCandidate(
        external_listing_id="227269664481",
        title="Aqara G400 mount",
        listing_type="FixedPriceItem",
        skus=skus,
        variation_specifics=specifics,
        quantity=4,
        is_migrated=is_migrated,
        detail_loaded=True,
    )


# --- The revise payload -------------------------------------------------------------


def test_revise_xml_includes_every_variation():
    """The load-bearing assertion of this whole file: all four variations must survive
    a revision that only renames them."""
    specifics = [{"Colour": c} for c in ("Black", "White", "Red", "Blue")]
    candidate = _candidate([f"OLD-{i}" for i in range(4)], specifics)

    xml = EbayAdapter._build_revise_skus_xml(candidate, [f"SKU-0012-{s}" for s in "ABCD"])
    root = ElementTree.fromstring(xml)

    variations = root.findall("e:Item/e:Variations/e:Variation", _NS)
    assert len(variations) == 4
    assert [v.findtext("e:SKU", "", _NS) for v in variations] == [f"SKU-0012-{s}" for s in "ABCD"]


def test_revise_xml_preserves_variation_identity():
    """A variation is identified by its specifics, not its SKU — dropping them would
    leave eBay unable to tell which variation is being renamed."""
    specifics = [{"Colour": "Black", "Size": "L"}, {"Colour": "White", "Size": "M"}]
    candidate = _candidate(["OLD-1", "OLD-2"], specifics)

    root = ElementTree.fromstring(EbayAdapter._build_revise_skus_xml(candidate, ["NEW-1", "NEW-2"]))
    first = root.findall("e:Item/e:Variations/e:Variation", _NS)[0]
    pairs = {
        nvl.findtext("e:Name", "", _NS): nvl.findtext("e:Value", "", _NS)
        for nvl in first.findall("e:VariationSpecifics/e:NameValueList", _NS)
    }
    assert pairs == {"Colour": "Black", "Size": "L"}


def test_revise_xml_single_sku_listing():
    candidate = _candidate(["OLD"], None)
    root = ElementTree.fromstring(EbayAdapter._build_revise_skus_xml(candidate, ["WIDGET-1"]))

    assert root.findtext("e:Item/e:SKU", "", _NS) == "WIDGET-1"
    assert root.find("e:Item/e:Variations", _NS) is None
    assert root.findtext("e:Item/e:ItemID", "", _NS) == "227269664481"


def test_revise_xml_escapes_values():
    """SKUs are free-text in StockSmith, so an ampersand in one must not corrupt the
    document (or inject elements into it)."""
    candidate = _candidate(["OLD"], None)
    xml = EbayAdapter._build_revise_skus_xml(candidate, ["A&B<C>"])

    ElementTree.fromstring(xml)  # would raise if the escaping were wrong
    assert "A&amp;B&lt;C&gt;" in xml


def test_xml_escape_covers_every_special_character():
    assert _xml_escape("&<>\"'") == "&amp;&lt;&gt;&quot;&apos;"


# --- The guard rails around it ------------------------------------------------------


def test_align_rejects_variation_count_mismatch():
    """The mistake that would delete variations. Must fail loudly, never send a partial
    Variations block."""
    candidate = _candidate(["A", "B", "C", "D"], [{"Colour": c} for c in ("Black", "White", "Red", "Blue")])
    with pytest.raises(PlatformSyncError, match="omitted ones"):
        _align_new_skus(candidate, ["only", "two"])


def test_align_rejects_empty_sku():
    candidate = _candidate(["A", "B"], [{"Colour": "Black"}, {"Colour": "White"}])
    with pytest.raises(PlatformSyncError, match="non-empty SKU"):
        _align_new_skus(candidate, ["NEW-1", "  "])


def test_align_rejects_wrong_count_on_single_sku_listing():
    candidate = _candidate(["A"], None)
    with pytest.raises(PlatformSyncError, match="expected exactly 1 SKU"):
        _align_new_skus(candidate, ["one", "two"])


def test_align_trims_whitespace():
    candidate = _candidate(["A"], None)
    assert _align_new_skus(candidate, ["  WIDGET-1  "]) == ["WIDGET-1"]


async def test_revise_refuses_migrated_listing():
    """Post-migration the SKU is effectively immutable, so this path must refuse rather
    than issue a call that can't do what it claims. Reachable with no session or network
    because the guard runs before any request is built."""
    candidate = _candidate(["A"], None, is_migrated=True)
    adapter = EbayAdapter("id", "secret")
    with pytest.raises(PlatformSyncError, match="already migrated"):
        await adapter.revise_listing_skus(None, None, candidate, ["NEW"])


# --- Deciding whether to revise at all ----------------------------------------------


def test_plan_returns_none_when_everything_matches():
    """No-op means no live listing edit — this is what stops alignment from touching a
    shop that needs nothing."""
    product = Product(id=1, name="Mount", sku="SKU-0012")
    variants = [ProductVariant(id=10, product_id=1, variant_name="A", sku_suffix="A")]
    candidate = _candidate(["SKU-0012-A"], [{"Colour": "Black"}])

    assert plan_sku_alignment(product, variants, candidate, [(10, "SKU-0012-A")]) is None


def test_plan_rewrites_only_the_mapped_variation():
    """Variations the user didn't map must be echoed back unchanged, not dropped."""
    product = Product(id=1, name="Mount", sku="SKU-0012")
    variants = [ProductVariant(id=10, product_id=1, variant_name="A", sku_suffix="A")]
    candidate = _candidate(["OLD-1", "UNTOUCHED"], [{"Colour": "Black"}, {"Colour": "White"}])

    assert plan_sku_alignment(product, variants, candidate, [(10, "OLD-1")]) == ["SKU-0012-A", "UNTOUCHED"]


def test_plan_handles_single_sku_listing():
    product = Product(id=1, name="Widget", sku="WIDGET")
    assert plan_sku_alignment(product, [], _candidate(["OLD"], None), [(None, "OLD")]) == ["WIDGET"]


def test_plan_returns_none_when_product_has_no_sku():
    """Nothing to align to — must not propose blanking eBay's SKU."""
    product = Product(id=1, name="Widget", sku=None)
    assert plan_sku_alignment(product, [], _candidate(["OLD"], None), [(None, "OLD")]) is None


# --- Idempotency and error surfacing ------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["Listing already migrated", "This SKU already exists", "Item is already an inventory item"],
)
def test_already_migrated_markers_are_recognised(message):
    assert _is_already_migrated_error({"errors": [{"message": message}]}) is True


def test_genuine_failure_is_not_mistaken_for_already_migrated():
    assert _is_already_migrated_error({"errors": [{"message": "Listing is not eligible for migration"}]}) is False


def test_no_errors_is_not_already_migrated():
    assert _is_already_migrated_error({}) is False


def test_trading_ack_failure_raises_despite_http_200():
    """The Trading API reports failure in the body — checking only the HTTP status would
    treat a rejected call as an empty success."""
    xml = """
    <GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Failure</Ack>
      <Errors><LongMessage>Auction listings cannot be migrated.</LongMessage></Errors>
    </GetItemResponse>
    """
    with pytest.raises(PlatformSyncError, match="Auction listings cannot be migrated"):
        _raise_for_trading_ack(ElementTree.fromstring(xml), "GetItem")


@pytest.mark.parametrize("ack", ["Success", "Warning"])
def test_trading_ack_success_and_warning_pass(ack):
    xml = f'<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>{ack}</Ack></GetItemResponse>'
    _raise_for_trading_ack(ElementTree.fromstring(xml), "GetItem")


def test_trading_auth_uses_iaf_token_not_bearer():
    """The Trading API takes the OAuth token in X-EBAY-API-IAF-TOKEN, NOT as
    `Authorization: Bearer` like every Sell REST call in the same adapter. Establishing
    this was the biggest risk to the whole Trading-API approach (see
    docs/plan-ebay-existing-store-onboarding.md), and it's an easy thing to "tidy" back
    into the shared _request_once helper — which would silently break every Trading call.
    """
    adapter = EbayAdapter("id", "secret")
    connection = SimpleNamespace(access_token="user-token")

    headers = adapter._trading_headers(connection, "GetItem", "3")

    assert headers["X-EBAY-API-IAF-TOKEN"] == "user-token"
    assert "Authorization" not in headers
    # DevID/AppName/CertName are ignored for these calls and StockSmith doesn't store a
    # DevID at all — sending empty ones would be worse than omitting them.
    assert not any(h.startswith("X-EBAY-API-DEV") for h in headers)
    assert headers["X-EBAY-API-CALL-NAME"] == "GetItem"
    assert headers["X-EBAY-API-SITEID"] == "3"


def test_site_id_table_covers_the_major_marketplaces():
    """A wrong site id silently returns the wrong (usually empty) listing set, which
    looks identical to "this seller has no unmigrated listings"."""
    assert _TRADING_SITE_IDS["UK"] == "3"
    assert _TRADING_SITE_IDS["US"] == "0"
    assert _TRADING_SITE_IDS["Germany"] == "77"
    assert _TRADING_SITE_IDS["Australia"] == "15"
