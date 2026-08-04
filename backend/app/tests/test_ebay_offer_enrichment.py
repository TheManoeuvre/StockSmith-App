"""Tests for enriching the eBay listing index from per-SKU Offers.

getInventoryItems alone carries no listing state and no variation data, so those were
previously hardcoded ("active", None) — the Platform Sync tab showed a permanently blank
eBay variation column and claimed every indexed SKU was live. Filling them in costs one
getOffers call per SKU (eBay requires `sku` on that endpoint and has no bulk equivalent),
which is why most of what's tested here is about *not* making those calls when they aren't
needed, and about failing softly when they don't come back.
"""

import asyncio

import httpx
import pytest

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms import ebay as ebay_module
from app.services.platforms.ebay import _MAX_CONCURRENT_OFFER_LOOKUPS, EbayAdapter
from app.services.platforms.etsy import EtsyAdapter


class _RoutedFakeClient:
    """Serves responses keyed on (path suffix, sku) rather than a FIFO queue.

    The existing _FakeAsyncClient in test_ebay_trading_transport pops a strict queue,
    which cannot express a concurrent fan-out: N offer lookups complete in nondeterministic
    order, so queue position stops corresponding to SKU. Also records in-flight
    concurrency so the semaphore is assertable rather than assumed.
    """

    inventory_pages: list[httpx.Response] = []
    offers: dict[str, httpx.Response] = {}
    token_response: httpx.Response | None = None
    requests: list[dict] = []
    in_flight = 0
    max_in_flight = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    # When set, any request whose bearer token equals this is answered 401 — modelling a
    # token eBay has stopped accepting, rather than "the first call always fails".
    rejected_token: str | None = None

    async def request(self, method, url, **kwargs):
        cls = _RoutedFakeClient
        params = kwargs.get("params") or {}
        headers = kwargs.get("headers") or {}
        cls.requests.append({"method": method, "url": url, "params": params, "headers": headers})

        cls.in_flight += 1
        cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        try:
            # A real await point, so concurrent lookups genuinely overlap and the
            # in-flight ceiling means something.
            await asyncio.sleep(0.01)
            # Scoped to /offer so the sequential inventory crawl doesn't absorb the 401
            # before the fan-out starts — that would refresh once, sequentially, and prove
            # nothing about concurrency. Models a token expiring mid-crawl.
            if (
                cls.rejected_token is not None
                and "/offer" in url
                and headers.get("Authorization") == f"Bearer {cls.rejected_token}"
            ):
                return _json({"errors": [{"message": "Invalid access token"}]}, status_code=401)
            if "/inventory_item" in url:
                return cls.inventory_pages.pop(0)
            if "/offer" in url:
                sku = params.get("sku")
                response = cls.offers.get(sku)
                if response is None:
                    raise AssertionError(f"no offer response registered for sku {sku!r}")
                # A list is a per-SKU queue, for simulating a first response that differs
                # from the retry (e.g. 401 then 200).
                if isinstance(response, list):
                    response = response.pop(0) if len(response) > 1 else response[0]
                if isinstance(response, Exception):
                    raise response
                return response
            if "identity" in url or "token" in url:
                return cls.token_response or _json({"access_token": "new", "expires_in": 7200})
            raise AssertionError(f"unexpected request to {url}")
        finally:
            cls.in_flight -= 1

    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)


@pytest.fixture(autouse=True)
def routed_http(monkeypatch):
    _RoutedFakeClient.inventory_pages = []
    _RoutedFakeClient.offers = {}
    _RoutedFakeClient.token_response = None
    _RoutedFakeClient.rejected_token = None
    _RoutedFakeClient.requests = []
    _RoutedFakeClient.in_flight = 0
    _RoutedFakeClient.max_in_flight = 0
    monkeypatch.setattr(ebay_module.httpx, "AsyncClient", _RoutedFakeClient)
    return _RoutedFakeClient


def _json(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _adapter() -> EbayAdapter:
    adapter = EbayAdapter("client-id", "client-secret", PlatformEnvironment.production)
    adapter._site_id = "3"
    return adapter


def _connection(expired: bool = False):
    from datetime import datetime, timedelta, timezone

    from app.models.platform_connection import PlatformConnection

    delta = timedelta(hours=-1) if expired else timedelta(hours=1)
    return PlatformConnection(
        access_token="tok",
        refresh_token="ref",
        access_token_expires_at=datetime.now(timezone.utc) + delta,
    )


def _item(sku: str, *, title: str | None = "A thing", aspects: dict | None = None, qty: int = 4) -> dict:
    product: dict = {}
    if title is not None:
        product["title"] = title
    if aspects is not None:
        product["aspects"] = aspects
    item: dict = {"sku": sku, "availability": {"shipToLocationAvailability": {"quantity": qty}}}
    if product:
        item["product"] = product
    return item


def _offer(listing_id: str = "L1", *, status: str = "PUBLISHED", listing_status: str | None = "ACTIVE") -> dict:
    offer: dict = {"offerId": f"OFF-{listing_id}", "status": status}
    listing: dict = {}
    if listing_id:
        listing["listingId"] = listing_id
    if listing_status is not None:
        listing["listingStatus"] = listing_status
    if listing:
        offer["listing"] = listing
    return offer


def _offers_response(*offers: dict) -> httpx.Response:
    return _json({"offers": list(offers)})


# --- Pure helpers ---------------------------------------------------------------------


def test_variation_format_matches_etsy_exactly():
    """The variation column is cross-platform and rendered by one shared table, so the two
    adapters' formatters must not drift. Asserting they agree makes drift a test failure
    rather than a cosmetic inconsistency nobody notices."""
    ebay_out = EbayAdapter._format_variation({"Size": ["Large"], "Colour": ["Caramel"]})
    etsy_out = EtsyAdapter._format_variation(
        [
            {"property_name": "Size", "values": ["Large"]},
            {"property_name": "Colour", "values": ["Caramel"]},
        ]
    )
    assert ebay_out == etsy_out == "Size: Large, Colour: Caramel"


def test_variation_is_none_when_there_are_no_aspects():
    assert EbayAdapter._format_variation({}) is None


@pytest.mark.parametrize(
    "status,listing_status,expected",
    [
        ("PUBLISHED", "ACTIVE", "active"),
        # A live GTC listing at qty 0 — very often 0 because StockSmith pushed 0. Anything
        # other than "active" here would flag this app's own correct behaviour as a sync
        # failure on every sold-out item.
        ("PUBLISHED", "OUT_OF_STOCK", "active"),
        ("PUBLISHED", "INACTIVE", "inactive"),
        ("PUBLISHED", "ENDED", "ended"),
        ("PUBLISHED", None, "active"),  # no listing container — trust `status`
        ("UNPUBLISHED", "ACTIVE", "unpublished"),
        # eBay can add enum members; inventing "active" for an unknown one would claim a
        # listing is live on no evidence.
        ("PUBLISHED", "SOMETHING_NEW", "something_new"),
    ],
)
def test_offer_state_mapping(status, listing_status, expected):
    offer = _offer(status=status, listing_status=listing_status)
    assert EbayAdapter._offer_state(offer) == expected


def test_select_offer_prefers_a_published_offer_with_a_listing():
    """A SKU can carry an auction and a fixed-price offer at once — a live listing must
    never be passed over in favour of an unpublished draft."""
    draft = _offer("", status="UNPUBLISHED", listing_status=None)
    live = _offer("L9")
    assert EbayAdapter._select_offer([draft, live]) is live
    assert EbayAdapter._select_offer([]) is None


def test_varying_aspects_strips_the_ones_that_do_not_vary():
    """An item's aspects can carry common specifics (Brand) alongside the varying ones;
    rendering "Brand: Acme" in a variation column would be wrong."""
    result = EbayAdapter._varying_aspects(
        {"L1": ["A", "B"]},
        {
            "A": {"Colour": ["Caramel"], "Brand": ["Acme"]},
            "B": {"Colour": ["Ivory"], "Brand": ["Acme"]},
        },
    )
    assert result["A"] == {"Colour": ["Caramel"]}
    assert result["B"] == {"Colour": ["Ivory"]}


def test_single_sku_listing_has_no_varying_aspects():
    """Matches Etsy, where a listing with one product and no property values gives None."""
    result = EbayAdapter._varying_aspects({"L1": ["A"]}, {"A": {"Colour": ["Caramel"]}})
    assert result["A"] == {}


def test_a_sku_missing_an_aspect_counts_as_varying():
    """Absent is different from equal — treating a missing aspect as "same" would drop the
    one thing distinguishing the two SKUs."""
    result = EbayAdapter._varying_aspects(
        {"L1": ["A", "B"]}, {"A": {"Colour": ["Caramel"]}, "B": {}}
    )
    assert result["A"] == {"Colour": ["Caramel"]}


# --- Through build_listing_sku_index --------------------------------------------------


async def test_variation_and_state_come_from_the_offer(routed_http):
    routed_http.inventory_pages = [
        _json(
            {
                "inventoryItems": [
                    _item("A", aspects={"Colour": ["Caramel"], "Brand": ["Acme"]}),
                    _item("B", aspects={"Colour": ["Ivory"], "Brand": ["Acme"]}),
                ],
                "total": 2,
            }
        )
    ]
    routed_http.offers = {"A": _offers_response(_offer("L1")), "B": _offers_response(_offer("L1"))}

    index = await _adapter().build_listing_sku_index(None, _connection())

    assert index["A"].variation == "Colour: Caramel"
    assert index["B"].variation == "Colour: Ivory"
    assert index["A"].state == "active"


async def test_out_of_stock_listing_stays_active(routed_http):
    routed_http.inventory_pages = [_json({"inventoryItems": [_item("A", qty=0)], "total": 1})]
    routed_http.offers = {"A": _offers_response(_offer("L1", listing_status="OUT_OF_STOCK"))}

    index = await _adapter().build_listing_sku_index(None, _connection())

    assert index["A"].state == "active"
    assert index["A"].quantity == 0


async def test_sku_with_no_offer_is_reported_as_such(routed_http):
    """An inventory item with no offer is real information — the SKU exists on eBay but
    nothing is listed. Strictly truer than the old hardcoded "active"."""
    routed_http.inventory_pages = [_json({"inventoryItems": [_item("A")], "total": 1})]
    routed_http.offers = {"A": _json({}, status_code=404)}

    index = await _adapter().build_listing_sku_index(None, _connection())

    assert index["A"].state == "no_offer"


async def test_missing_product_container_leaves_title_none(routed_http):
    """getInventoryItems (plural) is known to omit `product` entirely for some items that
    getInventoryItem (singular) returns in full."""
    routed_http.inventory_pages = [_json({"inventoryItems": [_item("A", title=None)], "total": 1})]
    routed_http.offers = {"A": _offers_response(_offer("L1"))}

    index = await _adapter().build_listing_sku_index(None, _connection())

    assert index["A"].title is None


async def test_one_failing_offer_does_not_fail_the_index(routed_http):
    """A flaky lookup must degrade one entry's detail, not repaint the whole catalogue as
    out-of-sync or abort the check."""
    routed_http.inventory_pages = [
        _json({"inventoryItems": [_item("A"), _item("B"), _item("C")], "total": 3})
    ]
    routed_http.offers = {
        "A": _offers_response(_offer("L1")),
        "B": httpx.ReadTimeout("boom"),
        "C": _offers_response(_offer("L3")),
    }

    index = await _adapter().build_listing_sku_index(None, _connection())

    assert set(index) == {"A", "B", "C"}
    # The failed one keeps its pass-1 placeholder rather than being downgraded.
    assert index["B"].state == "active"
    assert index["A"].state == "active"


# --- Scoping the fan-out --------------------------------------------------------------


def _offer_calls(routed_http) -> list[str]:
    return [r["params"].get("sku") for r in routed_http.requests if "/offer" in r["url"]]


async def test_enrich_false_makes_no_offer_calls(routed_http):
    """The two membership-only callers must pay nothing for enrichment."""
    routed_http.inventory_pages = [_json({"inventoryItems": [_item("A"), _item("B")], "total": 2})]

    index = await _adapter().build_listing_sku_index(None, _connection(), enrich=False)

    assert _offer_calls(routed_http) == []
    assert set(index) == {"A", "B"}  # still complete


async def test_enrich_skus_bounds_the_fan_out_without_narrowing_the_index(routed_http):
    """The hint scopes the *lookups*, never the index — callers rely on it for membership
    tests and would silently start missing SKUs if it filtered."""
    items = [_item(f"SKU-{i}") for i in range(20)]
    routed_http.inventory_pages = [_json({"inventoryItems": items, "total": 20})]
    routed_http.offers = {"SKU-1": _offers_response(_offer("L1")), "SKU-2": _offers_response(_offer("L2"))}

    index = await _adapter().build_listing_sku_index(
        None, _connection(), enrich_skus={"SKU-1", "SKU-2"}
    )

    assert sorted(_offer_calls(routed_http)) == ["SKU-1", "SKU-2"]
    assert len(index) == 20


async def test_enrich_skus_ignores_skus_ebay_does_not_have(routed_http):
    """A tracked SKU absent from eBay must not produce a doomed lookup."""
    routed_http.inventory_pages = [_json({"inventoryItems": [_item("A")], "total": 1})]
    routed_http.offers = {"A": _offers_response(_offer("L1"))}

    await _adapter().build_listing_sku_index(None, _connection(), enrich_skus={"A", "NOT-ON-EBAY"})

    assert _offer_calls(routed_http) == ["A"]


async def test_concurrency_is_capped(routed_http):
    routed_http.inventory_pages = [
        _json({"inventoryItems": [_item(f"SKU-{i}") for i in range(30)], "total": 30})
    ]
    routed_http.offers = {f"SKU-{i}": _offers_response(_offer(f"L{i}")) for i in range(30)}

    await _adapter().build_listing_sku_index(None, _connection())

    assert routed_http.max_in_flight <= _MAX_CONCURRENT_OFFER_LOOKUPS
    assert routed_http.max_in_flight > 1  # ...but it is genuinely concurrent


class _Session:
    """Minimal stand-in for AsyncSession — only commit() is reached from the refresh path,
    and counting it is the point."""

    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


async def test_expired_token_is_refreshed_once_before_the_fan_out(routed_http):
    """The cheap half: refreshing up front means the common case never contends."""
    routed_http.inventory_pages = [
        _json({"inventoryItems": [_item(f"SKU-{i}") for i in range(12)], "total": 12})
    ]
    routed_http.offers = {f"SKU-{i}": _offers_response(_offer(f"L{i}")) for i in range(12)}
    routed_http.token_response = _json({"access_token": "fresh", "expires_in": 7200})

    await _adapter().build_listing_sku_index(_Session(), _connection(expired=True))

    token_calls = [r for r in routed_http.requests if "token" in r["url"] or "identity" in r["url"]]
    assert len(token_calls) == 1


async def test_concurrent_401s_trigger_exactly_one_refresh(routed_http):
    """The real reason the refresh lock exists.

    _do_refresh commits on the caller's AsyncSession, which is NOT concurrency-safe. Every
    other eBay call in this adapter is sequential, but the offer fan-out is not: if eBay
    rejects the token mid-fan-out, N tasks hit the 401 path at once. Without the lock that
    is N refresh POSTs and N racing commits on one session, ending in a last-writer-wins
    token.

    Note this cannot be provoked by an expired token — the up-front _ensure_fresh handles
    that case before any concurrency starts, which is exactly why a 401 is used here.
    """
    skus = [f"SKU-{i}" for i in range(12)]
    routed_http.inventory_pages = [_json({"inventoryItems": [_item(s) for s in skus], "total": len(skus)})]
    routed_http.offers = {sku: _offers_response(_offer(f"L{sku}")) for sku in skus}
    routed_http.token_response = _json({"access_token": "fresh", "expires_in": 7200})
    # eBay stops accepting the current token partway through — every in-flight lookup
    # gets a 401 at once, which is the concurrency this lock exists for. Requests bearing
    # the refreshed token succeed, so a task that correctly skips a redundant refresh
    # still completes.
    routed_http.rejected_token = "tok"

    session = _Session()
    index = await _adapter().build_listing_sku_index(session, _connection())

    token_calls = [r for r in routed_http.requests if "token" in r["url"] or "identity" in r["url"]]
    assert len(token_calls) == 1, f"expected one refresh across {len(skus)} concurrent 401s"
    assert session.commits == 1
    # And the retries still succeeded, so the dedupe didn't leave anyone holding a stale token.
    assert all(index[sku].state == "active" for sku in skus)
