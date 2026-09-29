"""SquareAdapter.fetch_orders_since parsing, exercised against fixtures shaped like the
real sandbox spike's JSON (scripts/dev/square_sandbox_spike.py /
docs/plan-square-integration.md) rather than invented shapes. square_client's network
calls are monkeypatched — these are unit tests of the parsing logic, not of the HTTP layer
(square_client itself has no tests of its own yet, same as Etsy/eBay's request plumbing).
"""

from datetime import datetime, timezone

import pytest

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.services.platforms import square_client
from app.services.platforms.base import PaymentState
from app.services.platforms.errors import PlatformSyncError
from app.services.platforms.square import SquareAdapter

pytestmark = pytest.mark.asyncio


def _connection(**overrides) -> PlatformConnection:
    defaults = dict(
        platform=ListingPlatform.square,
        environment=PlatformEnvironment.sandbox,
        access_token="sandbox-token",
        external_account_id="LTKZ9N895XRAY",
        last_orders_synced_at=None,
    )
    defaults.update(overrides)
    return PlatformConnection(**defaults)


def _pickup_order(**overrides) -> dict:
    """Shaped like the sandbox spike's paid pickup order."""
    order = {
        "id": "Sks6KgrEqouH2mdiNn4MoVK3nf4F",
        "location_id": "LTKZ9N895XRAY",
        "created_at": "2026-09-29T13:38:32.670Z",
        "updated_at": "2026-09-29T13:38:34.017Z",
        "state": "OPEN",
        "line_items": [
            {
                "uid": "QW8RAf9XBcTpFJ36kQzHiB",
                "quantity": "1",
                "name": "Leather patch (custom)",
                "note": "Text on patch: HINETT'S HOME",
                "base_price_money": {"amount": 1500, "currency": "GBP"},
            },
            {
                "uid": "gGX8iDE5e22ruAq742ZFVC",
                "quantity": "1",
                "name": "Delivery",
                "base_price_money": {"amount": 350, "currency": "GBP"},
            },
        ],
        "fulfillments": [
            {
                "uid": "034XFZDlXauuGBjcQkJ5VF",
                "type": "PICKUP",
                "state": "PROPOSED",
                "pickup_details": {
                    "pickup_at": "2026-10-02T13:38:29.434Z",
                    "schedule_type": "SCHEDULED",
                    "recipient": {"display_name": "Test Customer"},
                },
            }
        ],
        "total_money": {"amount": 1850, "currency": "GBP"},
        "total_tax_money": {"amount": 0, "currency": "GBP"},
        "total_discount_money": {"amount": 0, "currency": "GBP"},
        "net_amount_due_money": {"amount": 0, "currency": "GBP"},
        "tenders": [{"id": "oTMmKfEPYq6yrYRouZgBERFveePZY", "payment_id": "oTMmKfEPYq6yrYRouZgBERFveePZY"}],
    }
    order.update(overrides)
    return order


def _payment_with_fee(amount: int = 71) -> dict:
    return {
        "payment": {
            "id": "oTMmKfEPYq6yrYRouZgBERFveePZY",
            "processing_fee": [{"type": "INITIAL", "amount_money": {"amount": amount, "currency": "GBP"}}],
        }
    }


def _mock_search_orders(monkeypatch, pages: list[dict]):
    calls = []

    async def _fake(access_token, environment, location_id, *, updated_since=None, cursor=None, limit=100):
        calls.append({"updated_since": updated_since, "cursor": cursor, "limit": limit})
        return pages.pop(0)

    monkeypatch.setattr(square_client, "search_orders", _fake)
    return calls


def _mock_get_payment(monkeypatch, response: dict | None = None):
    calls = []

    async def _fake(access_token, environment, payment_id):
        calls.append(payment_id)
        return (response or _payment_with_fee())["payment"]

    monkeypatch.setattr(square_client, "get_payment", _fake)
    return calls


def _mock_catalog(monkeypatch, objects: list[dict] | None = None):
    async def _fake(access_token, environment, object_ids):
        return objects or []

    monkeypatch.setattr(square_client, "batch_retrieve_catalog_objects", _fake)


async def test_requires_a_selected_location():
    adapter = SquareAdapter()
    connection = _connection(external_account_id=None)

    with pytest.raises(PlatformSyncError):
        await adapter.fetch_orders_since(session=None, connection=connection, since=None)


async def test_settlement_comes_from_net_amount_due_not_order_state(monkeypatch):
    """Regression test for the sandbox spike's finding: order.state stays 'OPEN' even
    when fully paid — settlement must be read from net_amount_due_money instead."""
    _mock_search_orders(monkeypatch, [{"orders": [_pickup_order()], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    assert len(orders) == 1
    assert orders[0].payment_state == PaymentState.settled


async def test_delivery_line_is_excluded_and_customisation_note_becomes_variation_text(monkeypatch):
    _mock_search_orders(monkeypatch, [{"orders": [_pickup_order()], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    lines = orders[0].lines
    assert len(lines) == 1
    assert lines[0].variation_text == "Text on patch: HINETT'S HOME"
    assert lines[0].unit_price == "15.00"


async def test_pickup_fulfilment_maps_to_collect_with_pickup_at(monkeypatch):
    _mock_search_orders(monkeypatch, [{"orders": [_pickup_order()], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    order = orders[0]
    assert order.fulfilment_method.value == "collect"
    assert order.collect_by == datetime(2026, 10, 2, 13, 38, 29, 434000, tzinfo=timezone.utc)


async def test_shipment_fulfilment_maps_to_delivery_with_no_collect_by(monkeypatch):
    shipment_order = _pickup_order(
        fulfillments=[
            {
                "uid": "abc",
                "type": "SHIPMENT",
                "state": "PROPOSED",
                "shipment_details": {
                    "recipient": {
                        "display_name": "Test Customer",
                        "address": {"address_line_1": "1 Test Street", "country": "GB"},
                    }
                },
            }
        ]
    )
    _mock_search_orders(monkeypatch, [{"orders": [shipment_order], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    order = orders[0]
    assert order.fulfilment_method.value == "delivery"
    assert order.collect_by is None


async def test_processing_fee_is_fetched_via_a_followup_payment_call(monkeypatch):
    get_payment_calls = _mock_get_payment(monkeypatch, _payment_with_fee(amount=71))
    _mock_search_orders(monkeypatch, [{"orders": [_pickup_order()], "cursor": None}])
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    assert get_payment_calls == ["oTMmKfEPYq6yrYRouZgBERFveePZY"]
    order = orders[0]
    assert order.payment_fees == "0.71"
    # total 18.50 - fee 0.71 = 17.79
    assert order.payment_net == "17.79"
    assert order.financials_enriched is True


async def test_unsettled_order_skips_the_processing_fee_lookup(monkeypatch):
    unpaid_order = _pickup_order(net_amount_due_money={"amount": 1850, "currency": "GBP"}, tenders=[])
    get_payment_calls = _mock_get_payment(monkeypatch)
    _mock_search_orders(monkeypatch, [{"orders": [unpaid_order], "cursor": None}])
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    assert get_payment_calls == []
    order = orders[0]
    assert order.payment_state == PaymentState.unsettled
    assert order.financials_enriched is False
    assert order.payment_fees is None


async def test_cancelled_never_paid_order_is_unsettled_not_reversed(monkeypatch):
    cancelled = _pickup_order(state="CANCELED", tenders=[], net_amount_due_money={"amount": 1500, "currency": "GBP"})
    _mock_search_orders(monkeypatch, [{"orders": [cancelled], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    order = orders[0]
    assert order.is_cancelled is True
    assert order.payment_state == PaymentState.unsettled


async def test_cancelled_previously_paid_order_is_reversed(monkeypatch):
    cancelled = _pickup_order(state="CANCELED", net_amount_due_money={"amount": 1500, "currency": "GBP"})
    _mock_search_orders(monkeypatch, [{"orders": [cancelled], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    order = orders[0]
    assert order.is_cancelled is True
    assert order.payment_state == PaymentState.reversed


async def test_sku_is_resolved_via_catalog_batch_retrieve(monkeypatch):
    order_with_catalog_line = _pickup_order()
    order_with_catalog_line["line_items"][0]["catalog_object_id"] = "CATALOG123"
    _mock_search_orders(monkeypatch, [{"orders": [order_with_catalog_line], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch, [{"id": "CATALOG123", "item_variation_data": {"sku": "PATCH-BLK"}}])

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    assert orders[0].lines[0].sku == "PATCH-BLK"


async def test_pagination_follows_the_cursor_across_pages(monkeypatch):
    page_1 = {"orders": [_pickup_order(id="order-1")], "cursor": "next-page"}
    page_2 = {"orders": [_pickup_order(id="order-2")], "cursor": None}
    calls = _mock_search_orders(monkeypatch, [page_1, page_2])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)

    orders = await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=None)

    assert [o.external_order_id for o in orders] == ["order-1", "order-2"]
    assert calls[0]["cursor"] is None
    assert calls[1]["cursor"] == "next-page"


async def test_since_is_passed_through_as_an_rfc3339_timestamp(monkeypatch):
    calls = _mock_search_orders(monkeypatch, [{"orders": [], "cursor": None}])
    _mock_get_payment(monkeypatch)
    _mock_catalog(monkeypatch)
    since = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    await SquareAdapter().fetch_orders_since(session=None, connection=_connection(), since=since)

    assert calls[0]["updated_since"] == "2026-09-01T12:00:00.000Z"
