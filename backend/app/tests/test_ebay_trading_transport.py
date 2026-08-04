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
from app.services.platforms.base import ClassicListingCandidate
from app.services.platforms.ebay import _DEFAULT_TRADING_SITE_ID, EbayAdapter
from app.services.platforms.errors import PlatformRateLimitError, PlatformSyncError


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient. Serves queued responses and records every
    request, so header/body construction is asserted against rather than assumed."""

    queue: list[httpx.Response] = []
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        # The timeout is configured on the client, not per-request, so it has to be
        # captured here to be assertable.
        self._client_timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        _FakeAsyncClient.requests.append(
            {"method": method, "url": url, "timeout": self._client_timeout, **kwargs}
        )
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


# --- Transient 5xx -------------------------------------------------------------------
#
# Live failure: bulkMigrateListing answered 500 with errorId 25001 ("A system error has
# occurred. Dependent service failure") — eBay's own label for a transient fault on their
# side, not a rejection. It failed hard on the first one, leaving the user staring at raw
# JSON with no indication that simply re-clicking would work.

_MIGRATE_5XX_BODY = {
    "responses": [
        {
            "statusCode": 500,
            "listingId": "226089819291",
            "marketplaceId": "EBAY_GB",
            "errors": [
                {
                    "errorId": 25001,
                    "domain": "API_INVENTORY",
                    "category": "REQUEST",
                    "message": "A system error has occurred. Dependent service failure",
                }
            ],
        }
    ]
}


@pytest.fixture
def no_sleep(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(ebay_module.asyncio, "sleep", _no_sleep)


async def test_transient_5xx_is_retried_and_can_succeed(fake_http, no_sleep):
    """The whole point: a 25001 that clears on retry must never reach the user."""
    fake_http.queue = [
        _json(_MIGRATE_5XX_BODY, status_code=500),
        _json(
            {"responses": [{"statusCode": 200, "listingId": "226089819291",
                            "inventoryItems": [{"sku": "SKU-0031-45-DEGREES"}]}]}
        ),
    ]

    result = await _adapter().migrate_listing(None, _connection(), "226089819291")

    assert result.inventory_item_skus == ["SKU-0031-45-DEGREES"]
    assert len(fake_http.requests) == 2  # initial + 1 retry


async def test_5xx_retry_budget_is_bounded(fake_http, no_sleep):
    """A persistent outage must not retry forever, and once the budget is spent the
    caller's own operation-specific message is what surfaces — not a generic transport
    error raised from _authed_request."""
    fake_http.queue = [_json(_MIGRATE_5XX_BODY, status_code=500) for _ in range(4)]

    with pytest.raises(PlatformSyncError) as exc:
        await _adapter().migrate_listing(None, _connection(), "226089819291")

    assert len(fake_http.requests) == 3  # initial + _MAX_SERVER_ERROR_RETRIES (2)
    assert "Failed to migrate eBay listing: 500" in str(exc.value)


async def test_4xx_is_not_retried(fake_http, no_sleep):
    """A 400 is a rejection of the request — retrying it is pure waste and would delay
    a real error reaching the user."""
    fake_http.queue = [_json({"errors": [{"message": "bad request"}]}, status_code=400)]

    with pytest.raises(PlatformSyncError):
        await _adapter().migrate_listing(None, _connection(), "226089819291")

    assert len(fake_http.requests) == 1


async def test_rate_limit_and_server_error_budgets_are_independent(fake_http, no_sleep):
    """A call unlucky enough to hit both must not find one budget consumed by the other."""
    fake_http.queue = [
        httpx.Response(429, text=""),
        _json(_MIGRATE_5XX_BODY, status_code=500),
        httpx.Response(429, text=""),
        _json(
            {"responses": [{"statusCode": 200, "listingId": "1", "inventoryItems": [{"sku": "S"}]}]}
        ),
    ]

    result = await _adapter().migrate_listing(None, _connection(), "1")

    assert result.inventory_item_skus == ["S"]
    assert len(fake_http.requests) == 4


async def test_trading_5xx_is_retried(fake_http, no_sleep):
    """The Trading API's XML surface has the same fault mode and its own retry loop."""
    fake_http.queue = [
        _xml("<html>502 Bad Gateway</html>", status_code=502),
        _xml(_active_list(_ITEM.format(item_id="1", title="One"))),
        _json({"inventoryItems": [], "total": 0}),  # build_listing_sku_index
    ]

    candidates = await _adapter().fetch_classic_listings(None, _connection())

    assert [c.external_listing_id for c in candidates] == ["1"]
    assert len(fake_http.requests) == 3  # failed call + retry + index build


# --- Timeouts ------------------------------------------------------------------------
#
# Live failure on a 4-variation listing: bulkMigrateListing blew through the blanket 15s
# timeout and httpx.ReadTimeout escaped as "Internal server error". Two things were
# wrong — the budget, and the fact that a network failure wasn't translated into
# anything a caller could act on.


class _TimingOutClient(_FakeAsyncClient):
    async def request(self, method, url, **kwargs):
        _FakeAsyncClient.requests.append({"method": method, "url": url, **kwargs})
        raise httpx.ReadTimeout("timed out")


async def test_ordinary_reads_keep_the_tight_default(fake_http):
    """The long budget must be scoped to the two heavy writes — a hung read should still
    fail fast rather than block a user-facing action for three minutes."""
    fake_http.queue = [_xml(_GET_ITEM)]

    await _adapter().fetch_classic_listing_detail(None, _connection(), "1")

    assert fake_http.requests[0]["timeout"] == ebay_module._DEFAULT_TIMEOUT


async def test_migration_gets_a_longer_budget_than_the_default(fake_http):
    """A migration creates an inventory item and an offer per variation before it
    answers, so it cannot share the tight default used for ordinary reads."""
    fake_http.queue = [_json({"responses": [{"listingId": "1", "statusCode": 200, "inventoryItems": []}]})]

    await _adapter().migrate_listing(None, _connection(), "1")

    assert fake_http.requests[0]["timeout"] == ebay_module._MIGRATION_TIMEOUT
    assert ebay_module._MIGRATION_TIMEOUT > ebay_module._DEFAULT_TIMEOUT


async def test_migration_timeout_says_it_may_have_happened_anyway(monkeypatch):
    """The dangerous phrasing would be "migration failed" — eBay very likely finished
    after we stopped listening, and telling the user nothing happened would invite them
    to assume the listing is untouched."""
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _TimingOutClient)

    with pytest.raises(PlatformSyncError) as exc:
        await _adapter().migrate_listing(None, _connection(), "227269664481")

    message = str(exc.value)
    assert "may still have completed" in message
    assert "again" in message  # tells them to re-run
    assert "227269664481" in message


async def test_revise_timeout_says_it_may_have_applied(monkeypatch):
    candidate = ClassicListingCandidate(
        external_listing_id="1",
        title="t",
        listing_type="FixedPriceItem",
        skus=["OLD"],
        variation_specifics=None,
        quantity=1,
        is_migrated=False,
        detail_loaded=True,
    )
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _TimingOutClient)
    adapter = _adapter()

    with pytest.raises(PlatformSyncError) as exc:
        await adapter.revise_listing_skus(None, _connection(), candidate, ["NEW"])

    assert "may still have applied" in str(exc.value)


async def test_timeouts_never_escape_as_raw_httpx_errors(monkeypatch):
    """The actual production symptom: an httpx exception reaching FastAPI unhandled
    becomes a bare "Internal server error". Every eBay call must surface as a
    PlatformError so the routers can map it to something meaningful."""
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _TimingOutClient)
    adapter = _adapter()

    for call in (
        lambda: adapter.fetch_classic_listing_detail(None, _connection(), "1"),
        lambda: adapter.build_listing_sku_index(None, _connection()),
        lambda: adapter.migrate_listing(None, _connection(), "1"),
    ):
        with pytest.raises(PlatformSyncError):
            await call()


async def test_timeout_is_an_ebay_timeout_subclass(monkeypatch):
    """EbayTimeout must remain a PlatformSyncError, or existing callers would stop
    handling it and it would escape as a 500 all over again."""
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _TimingOutClient)

    with pytest.raises(ebay_module.EbayTimeout):
        await _adapter().build_listing_sku_index(None, _connection())

    assert issubclass(ebay_module.EbayTimeout, PlatformSyncError)


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
