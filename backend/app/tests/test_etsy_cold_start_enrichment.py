"""A cold-watermark Etsy fetch must not re-enrich orders that are already enriched.

With no last_orders_synced_at the enrichment gate in EtsyAdapter._parse_receipt is open
for every settled receipt back to sync_start_date, and each shipped one costs a payment
lookup plus a paged 30-day ledger crawl. For a shop that already has those orders (and
their payment breakdowns) imported, that is thousands of API calls to learn nothing —
the multiplier behind the 2026-09-14 hour-long sync. Same-shop reconnects no longer
arrive here cold (see test_reconnect_keeps_watermark), but a first connect or a
different-shop reconnect still does, and this is the cap on what those can cost.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx

from app.models.listing import ListingPlatform
from app.models.order import Order, OrderStatus
from app.services.platforms.etsy import EtsyAdapter

_NOW = int(datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc).timestamp())


def _receipt(receipt_id: int, *, shipped: bool) -> dict:
    return {
        "receipt_id": receipt_id,
        "status": "paid",
        "is_paid": True,
        "is_shipped": shipped,
        "name": "A Buyer",
        "create_timestamp": _NOW - 3600,
        "update_timestamp": _NOW - 60,
        "grandtotal": {"amount": 1250, "divisor": 100, "currency_code": "GBP"},
        "transactions": [{"transaction_id": receipt_id * 10, "sku": "SKU-1", "quantity": 1}],
    }


def _adapter_serving(receipts: list[dict]) -> tuple[EtsyAdapter, list[str]]:
    """An adapter whose HTTP layer answers the receipts page from `receipts` and every
    enrichment endpoint with an empty 200, recording each path it was asked for."""
    adapter = EtsyAdapter("id", "secret")
    calls: list[str] = []

    async def _authed_request(session, connection, method, path, **kwargs):
        calls.append(path)
        if path.endswith("/receipts"):
            body = {"count": len(receipts), "results": receipts}
        else:
            body = {"count": 0, "results": []}
        return httpx.Response(200, json=body, request=httpx.Request(method, f"https://x{path}"))

    adapter._authed_request = _authed_request
    return adapter, calls


async def _stored_order(session, receipt_id: int, *, shipped: bool, enriched: bool = True) -> Order:
    order = Order(
        platform=ListingPlatform.etsy,
        external_order_id=str(receipt_id),
        status=OrderStatus.shipped if shipped else OrderStatus.pending,
        shipped_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if shipped else None,
        payment_fees=Decimal("1.23") if enriched else None,
        payment_net=Decimal("10.00") if enriched else None,
    )
    session.add(order)
    await session.commit()
    return order


def _enrichment_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if "/payments" in c or "/ledger-entries" in c]


async def test_cold_fetch_skips_receipts_already_enriched_locally(session):
    await _stored_order(session, 1, shipped=True)  # enriched, shipped: nothing left to learn
    await _stored_order(session, 2, shipped=False)  # enriched pre-shipment, still unshipped
    await _stored_order(session, 3, shipped=False, enriched=False)  # imported, breakdown missing
    # 4: never imported

    adapter, calls = _adapter_serving(
        [_receipt(1, shipped=True), _receipt(2, shipped=False), _receipt(3, shipped=False), _receipt(4, shipped=True)]
    )
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)

    orders = await adapter.fetch_orders_since(session, connection, None)

    by_id = {o.external_order_id: o for o in orders}
    assert by_id["1"].financials_enriched is False
    assert by_id["2"].financials_enriched is False
    assert by_id["3"].financials_enriched is True
    assert by_id["4"].financials_enriched is True
    # Only receipts 3 and 4 spent anything beyond the receipts page.
    assert {c.split("/receipts/")[1].split("/")[0] for c in calls if "/receipts/" in c} == {"3", "4"}


async def test_cold_fetch_still_enriches_an_order_shipped_since_it_was_stored(session):
    """Fees only post to the ledger on shipment, so a breakdown stored pre-shipment is
    the narrow card-processing figure — worth the calls to replace."""
    await _stored_order(session, 5, shipped=False)

    adapter, calls = _adapter_serving([_receipt(5, shipped=True)])
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)

    (order,) = await adapter.fetch_orders_since(session, connection, None)

    assert order.financials_enriched is True
    assert _enrichment_calls(calls)


async def test_warm_fetch_ignores_the_local_check(session):
    """With a live watermark the gate is already as tight as it can be, and an order the
    marketplace says changed *should* be re-enriched — the local shortcut must not
    suppress that. Also keeps the DB query off the every-15-minutes path."""
    await _stored_order(session, 6, shipped=True)

    adapter, calls = _adapter_serving([_receipt(6, shipped=True)])
    watermark = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=watermark)

    (order,) = await adapter.fetch_orders_since(session, connection, watermark)

    assert order.financials_enriched is True
    assert _enrichment_calls(calls)
