"""Transport-level tests for the eBay Trading API: real response documents in, parsed
objects out, with only the HTTP client faked.

Motivated by a production bug. Every other test in this suite mocks the adapter, so none
of them exercise what the API actually returns or how it's parsed — which is exactly how
"eBay's token response contains a `scope` field" (it doesn't) reached a release. These
tests can't prove eBay's real payloads match these fixtures, but they do catch the whole
class of failure that assumption belongs to: wrong XPath, missing namespace, parsing an
envelope as if it were a fragment, pagination that stops early.

That class matters disproportionately here because its failure mode is silent — a bad
XPath yields zero listings, which is indistinguishable in the UI from a seller who
genuinely has none.
"""

import httpx
import pytest

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms import ebay as ebay_module
from app.services.platforms.ebay import _DEFAULT_TRADING_SITE_ID, EbayAdapter
from app.services.platforms.errors import PlatformRateLimitError, PlatformSyncError


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient. Serves queued responses and records every
    request, so header/body construction is asserted against rather than assumed."""

    queue: list[httpx.Response] = []
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        _FakeAsyncClient.requests.append({"method": method, "url": url, **kwargs})
        if not _FakeAsyncClient.queue:
            raise AssertionError("no queued response for request to " + str(url))
        return _FakeAsyncClient.queue.pop(0)


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    _FakeAsyncClient.queue = []
    _FakeAsyncClient.requests = []
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def _xml(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, text=body.strip())


def _json(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _adapter() -> EbayAdapter:
    adapter = EbayAdapter("client-id", "client-secret", PlatformEnvironment.production)
    adapter._site_id = "3"  # skip the GetUser bootstrap except where it's under test
    return adapter


def _connection():
    from datetime import datetime, timedelta, timezone

    from app.models.platform_connection import PlatformConnection

    return PlatformConnection(
        access_token="tok",
        refresh_token="ref",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


# --- GetMyeBaySelling envelope -------------------------------------------------------

_ACTIVE_LIST_PAGE = """
<?xml version="1.0" encoding="UTF-8"?>
<GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Timestamp>2026-08-03T12:00:00.000Z</Timestamp>
  <Ack>Success</Ack>
  <Version>1413</Version>
  <ActiveList>
    <PaginationResult>
      <TotalNumberOfPages>{pages}</TotalNumberOfPages>
      <TotalNumberOfEntries>{entries}</TotalNumberOfEntries>
    </PaginationResult>
    <ItemArray>
      {items}
    </ItemArray>
  </ActiveList>
</GetMyeBaySellingResponse>
"""

_ITEM = """
      <Item>
        <ItemID>{item_id}</ItemID>
        <Title>{title}</Title>
        <ListingType>FixedPriceItem</ListingType>
        <SellingStatus><QuantityAvailable>4</QuantityAvailable></SellingStatus>
      </Item>
"""


def _active_list(items_xml: str, pages: int = 1, entries: int = 1) -> str:
    return _ACTIVE_LIST_PAGE.format(pages=pages, entries=entries, items=items_xml)


async def test_parses_active_list_envelope(fake_http):
    """The envelope path (ActiveList > ItemArray > Item) has never been exercised — only
    a bare <Item> fragment was, which would still pass if the nesting were wrong."""
    fake_http.queue = [
        _xml(_active_list(_ITEM.format(item_id="227269664481", title="Aqara G400 mount"))),
        _json({"inventoryItems": [], "total": 0}),  # build_listing_sku_index
    ]

    candidates = await _adapter().fetch_classic_listings(None, _connection())

    assert len(candidates) == 1
    assert candidates[0].external_listing_id == "227269664481"
    assert candidates[0].title == "Aqara G400 mount"
    assert candidates[0].quantity == 4
    assert candidates[0].detail_loaded is False


async def test_paginates_until_last_page(fake_http):
    page1 = _active_list(_ITEM.format(item_id="1", title="One"), pages=2, entries=2)
    page2 = _active_list(_ITEM.format(item_id="2", title="Two"), pages=2, entries=2)
    fake_http.queue = [_xml(page1), _xml(page2), _json({"inventoryItems": [], "total": 0})]

    candidates = await _adapter().fetch_classic_listings(None, _connection())

    assert [c.external_listing_id for c in candidates] == ["1", "2"]
    trading_calls = [r for r in fake_http.requests if "api.dll" in str(r["url"])]
    assert len(trading_calls) == 2
    assert "<PageNumber>1</PageNumber>" in trading_calls[0]["content"]
    assert "<PageNumber>2</PageNumber>" in trading_calls[1]["content"]


async def test_empty_active_list_is_not_an_error(fake_http):
    """A seller with nothing un-migrated is a normal outcome, not a failure."""
    fake_http.queue = [
        _xml(_active_list("", pages=1, entries=0)),
        _json({"inventoryItems": [], "total": 0}),
    ]

    assert await _adapter().fetch_classic_listings(None, _connection()) == []


async def test_already_migrated_listings_are_flagged(fake_http):
    """is_migrated comes from cross-referencing the Inventory API, not from the Trading
    response — so it needs the two payloads to line up."""
    item = """
      <Item>
        <ItemID>1</ItemID><Title>Mug</Title><ListingType>FixedPriceItem</ListingType>
        <SKU>MUG-1</SKU>
        <SellingStatus><QuantityAvailable>2</QuantityAvailable></SellingStatus>
      </Item>
    """
    fake_http.queue = [
        _xml(_active_list(item)),
        _json({"inventoryItems": [{"sku": "MUG-1", "product": {"title": "Mug"}}], "total": 1}),
    ]

    candidates = await _adapter().fetch_classic_listings(None, _connection())

    assert candidates[0].is_migrated is True


async def test_ack_failure_inside_a_200_raises(fake_http):
    """The Trading API returns HTTP 200 on rejection; only the body says otherwise."""
    fake_http.queue = [
        _xml(
            """
        <GetMyeBaySellingResponse xmlns="urn:ebay:apis:eBLBaseComponents">
          <Ack>Failure</Ack>
          <Errors><ShortMessage>Invalid token</ShortMessage>
            <LongMessage>Auth token is invalid or expired.</LongMessage></Errors>
        </GetMyeBaySellingResponse>
        """
        )
    ]

    with pytest.raises(PlatformSyncError, match="Auth token is invalid"):
        await _adapter().fetch_classic_listings(None, _connection())


# --- GetItem detail ------------------------------------------------------------------

_GET_ITEM = """
<?xml version="1.0" encoding="UTF-8"?>
<GetItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Ack>Success</Ack>
  <Item>
    <ItemID>227269664481</ItemID>
    <Title>Aqara G400 mount</Title>
    <ListingType>FixedPriceItem</ListingType>
    <SellingStatus><QuantityAvailable>9</QuantityAvailable></SellingStatus>
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
</GetItemResponse>
"""


async def test_get_item_returns_variation_detail(fake_http):
    """The whole reason GetItem exists in this feature — ActiveList can't be trusted for
    variation SKUs."""
    fake_http.queue = [_xml(_GET_ITEM)]

    candidate = await _adapter().fetch_classic_listing_detail(None, _connection(), "227269664481")

    assert candidate.detail_loaded is True
    assert candidate.skus == ["SKU-0012-A", "SKU-0012-B"]
    assert candidate.variation_specifics == [{"Colour": "Black"}, {"Colour": "White"}]
    assert candidate.ineligibility_reasons == []


async def test_get_item_sends_include_variations(fake_http):
    """Without IncludeVariations eBay omits the block entirely and the detail call would
    be no better than the list view it exists to correct."""
    fake_http.queue = [_xml(_GET_ITEM)]

    await _adapter().fetch_classic_listing_detail(None, _connection(), "227269664481")

    body = fake_http.requests[0]["content"]
    assert "<IncludeVariations>true</IncludeVariations>" in body
    assert "<ItemID>227269664481</ItemID>" in body
    assert fake_http.requests[0]["headers"]["X-EBAY-API-CALL-NAME"] == "GetItem"


async def test_get_item_escapes_the_item_id(fake_http):
    """Item id reaches this from a URL path parameter."""
    fake_http.queue = [_xml(_GET_ITEM)]

    await _adapter().fetch_classic_listing_detail(None, _connection(), "1&2<3")

    assert "1&amp;2&lt;3" in fake_http.requests[0]["content"]


# --- Site id resolution --------------------------------------------------------------


async def test_resolve_site_id_from_get_user(fake_http):
    fake_http.queue = [
        _xml(
            """
        <GetUserResponse xmlns="urn:ebay:apis:eBLBaseComponents">
          <Ack>Success</Ack>
          <User><UserID>seller</UserID><Site>UK</Site></User>
        </GetUserResponse>
        """
        )
    ]
    adapter = EbayAdapter("id", "secret")

    assert await adapter._resolve_site_id(None, _connection()) == "3"


async def test_resolve_site_id_caches(fake_http):
    """One GetUser per process, not per call — the Trading budget is 5,000/day."""
    fake_http.queue = [
        _xml(
            '<GetUserResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>'
            "<User><Site>Germany</Site></User></GetUserResponse>"
        )
    ]
    adapter = EbayAdapter("id", "secret")

    first = await adapter._resolve_site_id(None, _connection())
    second = await adapter._resolve_site_id(None, _connection())

    assert first == second == "77"
    assert len(fake_http.requests) == 1


async def test_unmapped_site_falls_back_rather_than_raising(fake_http):
    """An unrecognised site must not break the picker outright."""
    fake_http.queue = [
        _xml(
            '<GetUserResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack>'
            "<User><Site>Narnia</Site></User></GetUserResponse>"
        )
    ]

    assert await EbayAdapter("id", "s")._resolve_site_id(None, _connection()) == _DEFAULT_TRADING_SITE_ID


async def test_get_user_failure_falls_back(fake_http):
    fake_http.queue = [_xml("<html>gateway error</html>", status_code=500)]

    assert await EbayAdapter("id", "s")._resolve_site_id(None, _connection()) == _DEFAULT_TRADING_SITE_ID


# --- Auth header and rate limiting ---------------------------------------------------


async def test_trading_request_uses_iaf_token_header(fake_http):
    fake_http.queue = [_xml(_GET_ITEM)]

    await _adapter().fetch_classic_listing_detail(None, _connection(), "1")

    headers = fake_http.requests[0]["headers"]
    assert headers["X-EBAY-API-IAF-TOKEN"] == "tok"
    assert "Authorization" not in headers


async def test_trading_429_retries_then_raises(fake_http, monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(ebay_module.asyncio, "sleep", _no_sleep)
    fake_http.queue = [httpx.Response(429, text="") for _ in range(4)]

    with pytest.raises(PlatformRateLimitError):
        await _adapter().fetch_classic_listing_detail(None, _connection(), "1")

    assert len(fake_http.requests) == 4  # initial + 3 retries


# --- bulkMigrateListing --------------------------------------------------------------


async def test_migrate_parses_inventory_item_skus(fake_http):
    fake_http.queue = [
        _json(
            {
                "responses": [
                    {
                        "listingId": "227269664481",
                        "statusCode": 200,
                        "inventoryItems": [{"sku": "SKU-0012-A"}, {"sku": "SKU-0012-B"}],
                    }
                ]
            }
        )
    ]

    result = await _adapter().migrate_listing(None, _connection(), "227269664481")

    assert result.inventory_item_skus == ["SKU-0012-A", "SKU-0012-B"]


async def test_migrate_raises_on_genuine_rejection(fake_http):
    fake_http.queue = [
        _json(
            {
                "responses": [
                    {
                        "listingId": "1",
                        "statusCode": 400,
                        "errors": [{"message": "Listing is not eligible for migration"}],
                    }
                ]
            }
        )
    ]

    with pytest.raises(PlatformSyncError, match="rejected migration"):
        await _adapter().migrate_listing(None, _connection(), "1")


async def test_migrate_treats_already_migrated_as_success(fake_http):
    """Migration is irreversible, so a partially-failed adoption must be re-runnable."""
    fake_http.queue = [
        _json(
            {
                "responses": [
                    {"listingId": "1", "statusCode": 400, "errors": [{"message": "Listing already migrated"}]}
                ]
            }
        ),
        _xml(_GET_ITEM),  # read-back via GetItem
    ]

    result = await _adapter().migrate_listing(None, _connection(), "1")

    assert result.inventory_item_skus == ["SKU-0012-A", "SKU-0012-B"]


# --- ReviseFixedPriceItem ------------------------------------------------------------


async def test_revise_posts_all_variations_and_checks_ack(fake_http):
    fake_http.queue = [_xml(_GET_ITEM)]
    detail = await _adapter().fetch_classic_listing_detail(None, _connection(), "1")

    fake_http.queue = [
        _xml('<ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents"><Ack>Success</Ack></ReviseFixedPriceItemResponse>')
    ]
    await _adapter().revise_listing_skus(None, _connection(), detail, ["NEW-A", "NEW-B"])

    body = fake_http.requests[-1]["content"]
    assert body.count("<Variation>") == 2
    assert "NEW-A" in body and "NEW-B" in body
    assert fake_http.requests[-1]["headers"]["X-EBAY-API-CALL-NAME"] == "ReviseFixedPriceItem"


async def test_revise_surfaces_ebay_rejection(fake_http):
    fake_http.queue = [_xml(_GET_ITEM)]
    detail = await _adapter().fetch_classic_listing_detail(None, _connection(), "1")

    fake_http.queue = [
        _xml(
            """
        <ReviseFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">
          <Ack>Failure</Ack>
          <Errors><LongMessage>SKU is already used by another listing.</LongMessage></Errors>
        </ReviseFixedPriceItemResponse>
        """
        )
    ]

    with pytest.raises(PlatformSyncError, match="already used by another listing"):
        await _adapter().revise_listing_skus(None, _connection(), detail, ["DUP", "DUP2"])
