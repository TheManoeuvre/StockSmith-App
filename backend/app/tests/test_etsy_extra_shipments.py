"""A second Etsy parcel reaching an order through the receipt's `shipments` array
(order_parcels.apply_extra_shipments), independent of ledger-detected labels."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.notification import Notification
from app.models.order import Order
from app.models.order_parcel import OrderReplacementParcel, ReplacementParcelSource
from app.models.product import Product
from app.routers.orders import create_replacement_parcel
from app.schemas.order_parcel import ReplacementParcelCreate, ReplacementParcelItemInput
from app.services import order_sync
from app.services.platforms.base import ExternalShipment
from app.services.platforms.etsy import EtsyAdapter

from .conftest import make_order

_T0 = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def _ship(tracking: str, days: int = 0) -> ExternalShipment:
    return ExternalShipment(tracking_number=tracking, carrier="Royal Mail", shipped_at=_T0 + timedelta(days=days))


async def _sync(use_adapter, *shipments: ExternalShipment) -> None:
    use_adapter(
        [make_order("R-1", is_shipped=True, subtotal="20.00", shipping_charged="0.00", shipments=list(shipments))]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)


async def _parcels(session) -> list[OrderReplacementParcel]:
    return list((await session.execute(select(OrderReplacementParcel))).scalars())


async def test_second_shipment_spawns_review_parcel_once(session, connection, use_adapter, pushes):
    await _sync(use_adapter, _ship("T1"), _ship("T2", days=3))
    [parcel] = await _parcels(session)
    assert (parcel.tracking_number, parcel.carrier) == ("T2", "Royal Mail")
    assert parcel.source == ReplacementParcelSource.sync and parcel.needs_review is True
    [note] = list((await session.execute(select(Notification))).scalars())
    assert "T2" in note.body

    await _sync(use_adapter, _ship("T1"), _ship("T2", days=3))
    assert len(await _parcels(session)) == 1
    assert len(list((await session.execute(select(Notification))).scalars())) == 1


async def test_single_shipment_makes_no_parcel(session, connection, use_adapter, pushes):
    await _sync(use_adapter, _ship("T1"))
    assert await _parcels(session) == []


async def test_shipment_adopted_by_parcel_without_tracking(session, connection, use_adapter, pushes):
    await _sync(use_adapter, _ship("T1"))
    order = (await session.execute(select(Order))).scalar_one()
    session.add(
        OrderReplacementParcel(order_id=order.id, source=ReplacementParcelSource.manual, sent_at=_T0)
    )
    await session.commit()

    await _sync(use_adapter, _ship("T1"), _ship("T2", days=3))
    [parcel] = await _parcels(session)
    assert parcel.tracking_number == "T2" and parcel.source == ReplacementParcelSource.manual
    assert list((await session.execute(select(Notification))).scalars()) == []


async def test_completing_placeholder_retires_it_and_keeps_tracking(session, connection, use_adapter, pushes):
    product = Product(name="Widget", sku="WID", current_stock=5, allocated_qty=0)
    session.add(product)
    await session.commit()
    await _sync(use_adapter, _ship("T1"), _ship("T2", days=3))
    order = (await session.execute(select(Order))).scalar_one()
    [placeholder] = await _parcels(session)

    await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            completes_parcel_id=placeholder.id,
            postage_cost=Decimal("3.00"),
            items=[ReplacementParcelItemInput(product_id=product.id, qty=1)],
        ),
        session=session,
    )
    session.expire_all()
    [parcel] = await _parcels(session)
    # (ids can't distinguish them — SQLite reuses the freed rowid)
    assert parcel.needs_review is False
    assert parcel.source == ReplacementParcelSource.manual and parcel.tracking_number == "T2"

    # The next sync recognises T2 as handled rather than resurrecting a placeholder.
    await _sync(use_adapter, _ship("T1"), _ship("T2", days=3))
    assert len(await _parcels(session)) == 1


async def test_etsy_receipt_shipments_sorted_oldest_first():
    receipt = {
        "receipt_id": 1,
        "status": "open",
        "transactions": [],
        "shipments": [
            {"tracking_code": "LATE", "carrier_name": "DPD", "shipment_notification_timestamp": 2000},
            {"tracking_code": "FIRST", "carrier_name": "Royal Mail", "shipment_notification_timestamp": 1000},
        ],
    }
    connection = SimpleNamespace(external_account_id="9", last_orders_synced_at=None)
    order = await EtsyAdapter("id", "secret")._parse_receipt(None, connection, receipt)
    assert [s.tracking_number for s in order.shipments] == ["FIRST", "LATE"]
    assert order.tracking_number == "FIRST"
