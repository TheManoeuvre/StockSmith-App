"""Marketplace shipping labels reaching an order through the sync
(order_parcels.apply_postage_charges, driven end-to-end via order_sync.commit_sync).

Label #1 is the original shipment: its real cost replaces the shipping-profile estimate
in net profit. Every later label is a resend — it links to a parcel the user already
recorded, or spawns a needs_review one and raises an alert. Re-syncing the same labels
changes nothing, and the order surfaces under "awaiting" until the parcel is completed.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.notification import Notification, NotificationCategory
from app.models.order import Order, OrderStatus
from app.models.order_parcel import (
    OrderPostageCharge,
    OrderReplacementParcel,
    ReplacementParcelReason,
    ReplacementParcelSource,
)
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.routers.orders import (
    _serialize_one,
    create_replacement_parcel,
    delete_replacement_parcel,
    list_orders,
    update_replacement_parcel,
)
from app.schemas.order_parcel import ReplacementParcelCreate, ReplacementParcelUpdate
from app.services import order_sync
from app.services.platforms.base import ExternalPostageCharge

from .conftest import make_order

_T0 = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)


def _label(external_id: str, amount: str, *, days: int = 0) -> ExternalPostageCharge:
    return ExternalPostageCharge(
        external_id=external_id,
        amount=amount,
        currency="GBP",
        posted_at=_T0 + timedelta(days=days),
        description="Shipping label purchased",
    )


async def _product(session) -> Product:
    profile = ShippingProfile(name="Small", price=Decimal("3.60"), cost_etsy=Decimal("3.65"))
    session.add(profile)
    await session.flush()
    product = Product(name="Widget", sku="WID", current_stock=5, allocated_qty=0, shipping_profile_id=profile.id)
    session.add(product)
    await session.commit()
    return product


async def _sync(use_adapter, *labels: ExternalPostageCharge, **kwargs) -> Order:
    use_adapter(
        [
            make_order(
                "R-1",
                sku="WID",
                qty=1,
                is_shipped=True,
                subtotal="20.00",
                shipping_charged="0.00",
                postage_charges=list(labels),
                **kwargs,
            )
        ]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)


async def _order(session) -> Order:
    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    return order


async def _read(session, order_id: int):
    from app.routers.orders import _get_order_with_lines

    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


async def test_first_label_becomes_the_actual_postage_cost(session, connection, use_adapter, pushes):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"))

    order = await _order(session)
    assert order.status == OrderStatus.shipped
    read = await _read(session, order.id)
    [charge] = read.postage_charges
    assert (charge.sequence, charge.amount, charge.replacement_parcel_id) == (1, Decimal("3.10"), None)
    assert read.replacement_parcels == []
    # The profile figure is still there for comparison, but profit charges the real one.
    assert read.shipping_cost_snapshot == Decimal("3.65")
    assert read.postage_cost_actual == Decimal("3.10")
    assert read.postage_cost_effective == Decimal("3.10")
    assert read.postage_cost_missing is False
    assert read.net_profit == Decimal("20.00") - Decimal("3.10")
    assert read.replacement_parcels_need_review is False


async def test_second_label_spawns_a_review_parcel_and_an_alert(session, connection, use_adapter, pushes):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"), _label("L2", "3.40", days=5))

    order = await _order(session)
    read = await _read(session, order.id)
    [parcel] = read.replacement_parcels
    assert parcel.source == ReplacementParcelSource.sync
    assert parcel.reason == ReplacementParcelReason.unspecified
    assert parcel.needs_review is True
    assert parcel.items == []
    assert parcel.effective_postage == Decimal("3.40")
    assert parcel.postage_charge is not None and parcel.postage_charge.external_id == "L2"
    # SQLite hands datetimes back naive, so compare the wall-clock value.
    assert parcel.sent_at.replace(tzinfo=None) == (_T0 + timedelta(days=5)).replace(tzinfo=None)
    assert read.replacement_parcels_need_review is True
    assert read.replacement_postage == Decimal("3.40")
    assert read.net_profit == Decimal("20.00") - Decimal("3.10") - Decimal("3.40")

    [note] = list((await session.execute(select(Notification))).scalars())
    assert note.category == NotificationCategory.replacement_parcel_review
    assert note.related_entity_type == "order" and note.related_entity_id == order.id
    assert "R-1" in note.title and "3.40 GBP" in note.body
    assert note.read_at is None

    # It's shipped, but it needs a human — so it's in the awaiting feed, at the front.
    awaiting = await list_orders(status_filter="awaiting", limit=50, offset=0, session=session)
    assert [o.id for o in awaiting.items] == [order.id]
    assert awaiting.total == 1
    shipped = await list_orders(status_filter="shipped", limit=50, offset=0, session=session)
    assert [o.id for o in shipped.items] == [order.id]

    # Completing the parcel clears the alert and drops the order back out of awaiting.
    await update_replacement_parcel(
        parcel.id, ReplacementParcelUpdate(reason=ReplacementParcelReason.lost_in_transit, needs_review=False), session=session
    )
    await session.refresh(note)
    assert note.read_at is not None
    awaiting = await list_orders(status_filter="awaiting", limit=50, offset=0, session=session)
    assert awaiting.items == [] and awaiting.total == 0


async def test_resync_is_idempotent(session, connection, use_adapter, pushes):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"), _label("L2", "3.40", days=5))
    # Later sync: same labels again, plus nothing new. last_modified must pass the
    # watermark for the order to be re-enriched at all.
    await _sync(
        use_adapter,
        _label("L1", "3.10"),
        _label("L2", "3.40", days=5),
        last_modified=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    charges = list((await session.execute(select(OrderPostageCharge))).scalars())
    assert sorted((c.external_id, c.sequence) for c in charges) == [("L1", 1), ("L2", 2)]
    assert len(list((await session.execute(select(OrderReplacementParcel))).scalars())) == 1
    assert len(list((await session.execute(select(Notification))).scalars())) == 1


async def test_a_parcel_recorded_by_hand_gets_the_label_instead_of_a_new_parcel(
    session, connection, use_adapter, pushes
):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"))
    order = await _order(session)
    product = (await session.execute(select(Product))).scalar_one()
    read = await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            reason=ReplacementParcelReason.faulty_item,
            postage_cost=Decimal("3.00"),
            items=[{"product_id": product.id, "qty": 1}],
        ),
        session=session,
    )
    [manual] = read.replacement_parcels
    assert manual.effective_postage == Decimal("3.00")

    await _sync(
        use_adapter,
        _label("L1", "3.10"),
        _label("L2", "3.40", days=5),
        last_modified=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    read = await _read(session, order.id)
    [parcel] = read.replacement_parcels
    assert parcel.id == manual.id
    assert parcel.needs_review is False
    # The marketplace's figure now wins over the typed one, which stays as a note.
    assert parcel.postage_cost == Decimal("3.00")
    assert parcel.effective_postage == Decimal("3.40")
    assert (await session.execute(select(Notification))).scalar_one_or_none() is None


async def test_bulk_label_keeps_the_estimate_but_still_counts_as_label_one(session, connection, use_adapter, pushes):
    """Order 04-15163-59902 live: the original label was bought in a batch of six, so eBay
    reports it with the batch total and no per-order amount (ExternalPostageCharge.amount
    None). Profit must stay on the £3.65 profile estimate rather than charge £21.90, and
    the later £3.65 label must still be a resend — not promoted to the original."""
    await _product(session)
    bulk = _label("BULK", "0")
    bulk.amount = None
    bulk.description = "Bulk label purchase of 21.90 GBP across several orders"
    await _sync(use_adapter, bulk, _label("L2", "3.65", days=3))

    order = await _order(session)
    read = await _read(session, order.id)
    first = next(c for c in read.postage_charges if c.sequence == 1)
    assert (first.external_id, first.amount) == ("BULK", None)
    assert first.description == bulk.description
    assert read.postage_cost_actual is None
    assert read.postage_cost_effective == Decimal("3.65")
    assert read.postage_cost_missing is False
    [parcel] = read.replacement_parcels
    assert parcel.postage_charge is not None and parcel.postage_charge.external_id == "L2"
    assert parcel.effective_postage == Decimal("3.65")
    assert read.net_profit == Decimal("20.00") - Decimal("3.65") - Decimal("3.65")


async def test_bulk_resend_label_defers_to_the_typed_postage(session, connection, use_adapter, pushes):
    """A resend bought in bulk links to its parcel like any other label but has no cost of
    its own, so the parcel's typed postage is what profit uses — and the alert says the
    cost isn't itemised rather than printing a batch total."""
    await _product(session)
    bulk = _label("BULK", "0", days=5)
    bulk.amount = None
    await _sync(use_adapter, _label("L1", "3.10"), bulk)

    order = await _order(session)
    read = await _read(session, order.id)
    [parcel] = read.replacement_parcels
    assert parcel.needs_review is True
    assert parcel.postage_charge is not None and parcel.postage_charge.amount is None
    assert parcel.effective_postage is None
    assert read.replacement_postage == Decimal(0)
    [note] = list((await session.execute(select(Notification))).scalars())
    assert "not itemised" in note.body

    await update_replacement_parcel(
        parcel.id,
        ReplacementParcelUpdate(reason=ReplacementParcelReason.lost_in_transit, postage_cost=Decimal("3.65"), needs_review=False),
        session=session,
    )
    read = await _read(session, order.id)
    [parcel] = read.replacement_parcels
    assert parcel.postage_charge is not None and parcel.postage_charge.external_id == "BULK"
    assert parcel.effective_postage == Decimal("3.65")
    assert read.net_profit == Decimal("20.00") - Decimal("3.10") - Decimal("3.65")


async def test_resync_clears_a_stored_amount_the_marketplace_now_calls_bulk(session, connection, use_adapter, pushes):
    """Labels recorded before bulk purchases were recognised hold the batch total. The
    next sync that sees the same label reported as bulk clears it — the one direction a
    stored amount is ever changed."""
    await _product(session)
    await _sync(use_adapter, _label("BULK", "21.90"))
    read = await _read(session, (await _order(session)).id)
    assert read.postage_cost_effective == Decimal("21.90")

    bulk = _label("BULK", "0")
    bulk.amount = None
    bulk.description = "Bulk label purchase of 21.90 GBP across several orders"
    await _sync(use_adapter, bulk, last_modified=datetime.now(timezone.utc) + timedelta(minutes=5))

    read = await _read(session, (await _order(session)).id)
    [charge] = read.postage_charges
    assert (charge.sequence, charge.amount, charge.description) == (1, None, bulk.description)
    assert read.postage_cost_actual is None
    assert read.postage_cost_effective == Decimal("3.65")


async def test_a_label_already_on_another_order_is_skipped_not_duplicated(session, connection, use_adapter, pushes):
    """Live failure on 09-15158-06992: eBay returns a bulk purchase's single transaction for
    every order in the batch, so two orders report the same transactionId. The unique key
    is (platform, external_id), so the second order must skip it — not blow up the sync."""
    await _product(session)
    use_adapter(
        [
            make_order("R-1", sku="WID", qty=1, is_shipped=True, postage_charges=[_label("BULK", "21.90")]),
            make_order("R-2", sku="WID", qty=1, is_shipped=True, postage_charges=[_label("BULK", "21.90")]),
        ]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)

    charges = list((await session.execute(select(OrderPostageCharge))).scalars())
    [charge] = charges
    assert charge.external_id == "BULK"
    orders = list((await session.execute(select(Order).order_by(Order.id))).scalars())
    assert len(orders) == 2
    # Exactly one of the two orders holds the label; the other carries none.
    assert sorted(o.id == charge.order_id for o in orders) == [False, True]


async def test_unenriched_pass_leaves_labels_alone(session, connection, use_adapter, pushes):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"))
    await _sync(use_adapter, financials_enriched=False, last_modified=datetime.now(timezone.utc) + timedelta(minutes=5))

    charges = list((await session.execute(select(OrderPostageCharge))).scalars())
    assert [c.external_id for c in charges] == ["L1"]


async def test_deleting_a_sync_parcel_clears_its_alert_but_keeps_the_charge(
    session, connection, use_adapter, pushes
):
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"), _label("L2", "3.40", days=5))
    order = await _order(session)
    read = await _read(session, order.id)
    [parcel] = read.replacement_parcels

    read = await delete_replacement_parcel(parcel.id, session=session)
    assert read.replacement_parcels == []
    assert [c.external_id for c in read.postage_charges] == ["L1", "L2"]
    # Still the seller's money: a hidden prompt doesn't un-spend it.
    assert read.replacement_postage == Decimal("3.40")
    assert read.net_profit == Decimal("20.00") - Decimal("3.10") - Decimal("3.40")
    note = (await session.execute(select(Notification))).scalar_one()
    assert note.read_at is not None


async def test_recording_against_the_placeholder_label_replaces_the_placeholder(
    session, connection, use_adapter, pushes
):
    """The "complete this parcel" path from the UI: one create call carrying the label the
    sync's empty placeholder holds. The placeholder goes, its alert is resolved, and the
    real parcel takes the label — one transaction."""
    await _product(session)
    await _sync(use_adapter, _label("L1", "3.10"), _label("L2", "3.40", days=5))
    order = await _order(session)
    product = (await session.execute(select(Product))).scalar_one()
    read = await _read(session, order.id)
    [placeholder] = read.replacement_parcels

    read = await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            reason=ReplacementParcelReason.damaged_in_transit,
            postage_charge_id=placeholder.postage_charge.id,
            items=[{"product_id": product.id, "qty": 1}],
        ),
        session=session,
    )
    [parcel] = read.replacement_parcels
    assert parcel.source == ReplacementParcelSource.manual
    assert parcel.reason == ReplacementParcelReason.damaged_in_transit
    assert parcel.needs_review is False
    assert parcel.postage_charge is not None and parcel.postage_charge.external_id == "L2"
    assert parcel.effective_postage == Decimal("3.40")
    assert read.replacement_parcels_need_review is False
    note = (await session.execute(select(Notification))).scalar_one()
    assert note.read_at is not None
    await session.refresh(product)
    # 5 on hand, 1 shipped on the original order, 1 more in the replacement.
    assert product.current_stock == 3


@pytest.mark.parametrize("platform", [ListingPlatform.etsy])
async def test_manual_orders_never_get_charges(session, platform):
    """apply_postage_charges is a no-op without a platform — belt and braces, since the
    sync is the only caller and only ever passes synced orders."""
    from app.services.order_parcels import apply_postage_charges

    order = Order(status=OrderStatus.shipped, order_placed_at=_T0)
    session.add(order)
    await session.commit()
    assert await apply_postage_charges(session, order, [_label("X", "1.00")]) == []
    assert (await session.execute(select(OrderPostageCharge))).scalar_one_or_none() is None
