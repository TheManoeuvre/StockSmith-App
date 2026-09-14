"""eBay's ship_by_date parsing was always correct (unlike Etsy's — see
test_etsy_ship_by_date_and_personalization.py), computed per line item's
lineItemFulfillmentInstructions.shipByDate. But an order imported before that field
existed, or not touched by a sync since (fetch_orders_since only re-fetches orders whose
lastmodifieddate moved), is still stuck with ship_by_date=None.

scripts/backfill_order_tracking_and_variations.py fixes this for already-imported orders
via EbayAdapter.fetch_order (the single-order counterpart to the bulk getOrders call) and
EbayAdapter._ship_by_date_from_line_items (extracted from _parse_order so both the live
sync path and the backfill script compute the exact same value).
"""

from types import SimpleNamespace

from app.models.platform_connection import PlatformEnvironment
from app.services.platforms.ebay import EbayAdapter


def _adapter() -> EbayAdapter:
    return EbayAdapter("id", "secret", PlatformEnvironment.production)


def test_ship_by_date_from_line_items_takes_the_earliest():
    """A multi-line eBay order ships as one parcel, so the binding deadline is the
    earliest shipByDate across its line items."""
    line_items = [
        {"lineItemFulfillmentInstructions": {"shipByDate": "2026-09-20T00:00:00.000Z"}},
        {"lineItemFulfillmentInstructions": {"shipByDate": "2026-09-15T00:00:00.000Z"}},
    ]
    result = EbayAdapter._ship_by_date_from_line_items(line_items)
    assert result is not None
    assert result.isoformat() == "2026-09-15T00:00:00+00:00"


def test_ship_by_date_from_line_items_is_none_when_absent():
    line_items = [{"lineItemFulfillmentInstructions": {}}, {}]
    assert EbayAdapter._ship_by_date_from_line_items(line_items) is None


async def test_fetch_order_returns_body_on_200():
    adapter = _adapter()
    seen = {}

    async def fake_authed_request(session, connection, method, url, **kwargs):
        seen["method"], seen["url"] = method, url
        return SimpleNamespace(status_code=200, json=lambda: {"orderId": "25-15130-35706", "lineItems": []})

    adapter._authed_request = fake_authed_request

    result = await adapter.fetch_order(None, SimpleNamespace(), "25-15130-35706")

    assert result == {"orderId": "25-15130-35706", "lineItems": []}
    assert seen["method"] == "GET"
    assert seen["url"].endswith("/sell/fulfillment/v1/order/25-15130-35706")


async def test_fetch_order_returns_none_on_failure_rather_than_raising():
    """Matches this adapter's other best-effort per-order lookups (_fetch_tracking,
    _fetch_transactions) — a failed single-order lookup during backfill shouldn't crash
    the whole run, just skip that order."""
    adapter = _adapter()

    async def failing_request(session, connection, method, url, **kwargs):
        return SimpleNamespace(status_code=404, text="Not found")

    adapter._authed_request = failing_request

    result = await adapter.fetch_order(None, SimpleNamespace(), "25-00000-00000")

    assert result is None
