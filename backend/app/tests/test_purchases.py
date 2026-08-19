"""Purchase orders: receiving, reversing, closing short, and editing lines underneath.

None of this had a test before. Receiving was one endpoint that flipped a flag, and the
flag was cheap enough to believe by reading — which stopped being true the moment a line
could be delivered twice, on two dates, at two shares of one cost.

The test that matters most here is test_two_part_receipt_weighted_average. Everything else
guards a rule; that one *is* the feature, and it fails under the old whole-order model.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.material import Material, MaterialAdjustmentMode, MaterialCategory, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.routers.purchases import delete_purchase, receive_purchase, replace_purchase_lines
from app.schemas.purchase import PurchaseLineInput
from app.services import purchase_receipts
from app.services.costing import create_adjustment, get_on_order_qty_by_material
from app.services.platforms.base import ensure_utc

JAN1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
JAN2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
JAN3 = datetime(2026, 1, 3, tzinfo=timezone.utc)


async def _material(session, name="Filament", unit=MaterialUnit.g, reorder_threshold=0) -> Material:
    material = Material(
        name=name, category=MaterialCategory.filament, unit=unit, reorder_threshold=Decimal(reorder_threshold)
    )
    session.add(material)
    await session.flush()
    return material


async def _order(session, material_id: int, qty, total_cost) -> Purchase:
    purchase = Purchase(status=PurchaseStatus.ordered)
    purchase.lines = [
        MaterialPurchase(material_id=material_id, qty=Decimal(qty), total_cost=Decimal(total_cost))
    ]
    session.add(purchase)
    await session.commit()
    return purchase


async def _line_id(session, purchase: Purchase) -> int:
    reloaded = await purchase_receipts._load_purchase(session, purchase.id)
    return reloaded.lines[0].id


async def test_receive_in_full(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)

    received = await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1
    )

    assert received.status == PurchaseStatus.received
    assert ensure_utc(received.received_at) == JAN1
    assert received.lines[0].outstanding_qty == Decimal(0)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(10)
    assert Decimal(material.avg_unit_cost) == Decimal(10)


async def test_receive_in_part(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)

    received = await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(6), None)], received_at=JAN1
    )

    assert received.status == PurchaseStatus.partially_received
    # Still outstanding, so the order has no completion date yet.
    assert received.received_at is None
    assert received.lines[0].received_qty == Decimal(6)
    assert received.lines[0].outstanding_qty == Decimal(4)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(6)
    assert Decimal(material.avg_unit_cost) == Decimal(10)
    assert (await get_on_order_qty_by_material(session))[material.id] == Decimal(4)


async def test_two_part_receipt_weighted_average(session):
    """A line delivered across two dates costs at each date, not at one.

    Empty material. Order A is 10 at £100; six arrive on the 1st, so 6 @ £10.
    Order B is 10 at £200, all of it on the 2nd: 16 on hand, (60 + 200) / 16 = £16.25.
    Order A's remaining four arrive on the 3rd, carrying the £40 still unspent on that
    line: 20 on hand, (16 x 16.25 + 40) / 20 = £15.00.

    Under the whole-order model A could only land at a single moment, before or after B,
    and neither answer is £15.
    """
    material = await _material(session)
    order_a = await _order(session, material.id, 10, "100")
    order_b = await _order(session, material.id, 10, "200")
    line_a = await _line_id(session, order_a)
    line_b = await _line_id(session, order_b)

    await purchase_receipts.record_receipts(session, order_a.id, [(line_a, Decimal(6), None)], received_at=JAN1)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(6)
    assert Decimal(material.avg_unit_cost) == Decimal("10")

    await purchase_receipts.record_receipts(session, order_b.id, [(line_b, Decimal(10), None)], received_at=JAN2)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(16)
    assert Decimal(material.avg_unit_cost) == Decimal("16.25")

    await purchase_receipts.record_receipts(session, order_a.id, [(line_a, Decimal(4), None)], received_at=JAN3)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(20)
    assert Decimal(material.avg_unit_cost) == Decimal("15.00")


async def test_shares_of_a_split_line_add_up_to_the_line_total(session):
    """Three deliveries of a line whose total does not divide evenly still spend it exactly.

    100 units at £10 is 3-3-4; a third of £10 recurs. The last receipt takes the remainder
    rather than its own third, so nothing is stranded and the average lands on 0.10.
    """
    material = await _material(session)
    purchase = await _order(session, material.id, 100, "10")
    line_id = await _line_id(session, purchase)

    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(33), None)], received_at=JAN1)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(33), None)], received_at=JAN2)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(34), None)], received_at=JAN3)

    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(100)
    assert Decimal(material.avg_unit_cost) == Decimal("0.100000")


async def test_explicit_receipt_cost_overrides_pro_rata(session):
    """A delivery billed separately uses its own figure, and its line stops being swept."""
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)

    await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(5), Decimal("80"))], received_at=JAN1
    )
    await session.refresh(material)
    assert Decimal(material.avg_unit_cost) == Decimal("16")

    # The second half falls back to pro-rata (£50), not to "whatever is left of £100".
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(5), None)], received_at=JAN2)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(10)
    assert Decimal(material.avg_unit_cost) == Decimal("13")


async def test_over_receipt_is_rejected_and_changes_nothing(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(6), None)], received_at=JAN1)

    with pytest.raises(HTTPException) as exc:
        await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(5), None)])
    assert exc.value.status_code == 400
    assert "only 4" in exc.value.detail

    # The rollback matters as much as the status code — a rejected delivery that moved
    # stock anyway is worse than one that errored.
    await session.rollback()
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(6)


async def test_fractional_receipt_rejected_for_each_unit(session):
    material = await _material(session, name="Box", unit=MaterialUnit.each)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)

    with pytest.raises(HTTPException) as exc:
        await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal("2.5"), None)])
    assert exc.value.status_code == 400


async def test_reversing_a_receipt_restores_the_previous_state(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(6), None)], received_at=JAN1)
    await session.refresh(material)
    before_qty, before_cost = Decimal(material.current_qty), Decimal(material.avg_unit_cost)

    received = await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(4), None)], received_at=JAN2
    )
    second = received.lines[0].receipts[-1]
    reverted = await purchase_receipts.delete_receipts(session, purchase.id, [second.id])

    assert reverted.status == PurchaseStatus.partially_received
    await session.refresh(material)
    assert Decimal(material.current_qty) == before_qty
    assert Decimal(material.avg_unit_cost) == before_cost


async def test_unreceiving_everything_returns_the_order_to_ordered(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    received = await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1
    )

    reverted = await purchase_receipts.delete_receipts(
        session, purchase.id, [r.id for r in received.lines[0].receipts]
    )

    assert reverted.status == PurchaseStatus.ordered
    assert reverted.received_at is None
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(0)


async def test_close_short_completes_the_order_and_clears_on_order(session):
    """Seven of ten turned up and the rest is not coming.

    Without apportion_remainder the £30 share of what never arrived simply never enters
    inventory, which is right when the invoice matched the delivery: 7 @ £10.
    """
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(7), None)], received_at=JAN1)

    closed = await purchase_receipts.close_line(session, purchase.id, line_id)

    assert closed.status == PurchaseStatus.received
    assert closed.lines[0].outstanding_qty == Decimal(0)
    assert material.id not in await get_on_order_qty_by_material(session)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(7)
    assert Decimal(material.avg_unit_cost) == Decimal("10")


async def test_close_short_can_carry_the_whole_billed_cost(session):
    """Billed for all ten, sent seven: the seven carry £100, so £14.285714 each."""
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(7), None)], received_at=JAN1)

    await purchase_receipts.close_line(session, purchase.id, line_id, apportion_remainder=True)

    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(7)
    assert Decimal(material.avg_unit_cost) == Decimal("14.285714")


async def test_first_delivery_of_a_material_counts_as_a_stock_take(session):
    """An empty ledger means a provable zero, so the first delivery is a verified count."""
    material = await _material(session)
    assert material.last_stock_take_at is None
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)

    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1)

    await session.refresh(material)
    assert ensure_utc(material.last_stock_take_at) == JAN1


async def test_restocking_a_material_that_ran_out_is_not_a_count(session):
    """A zero reached by consumption is a belief, not an observation — so it stays due."""
    material = await _material(session)
    first = await _order(session, material.id, 10, "100")
    await purchase_receipts.record_receipts(
        session, first.id, [(await _line_id(session, first), Decimal(10), None)], received_at=JAN1
    )
    await create_adjustment(session, material.id, MaterialAdjustmentMode.adjust, Decimal(-10), "used up")
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(0)
    counted_at = material.last_stock_take_at

    second = await _order(session, material.id, 5, "50")
    await purchase_receipts.record_receipts(
        session, second.id, [(await _line_id(session, second), Decimal(5), None)], received_at=JAN3
    )

    await session.refresh(material)
    assert ensure_utc(material.last_stock_take_at) == ensure_utc(counted_at)  # unchanged by the restock


async def test_reversing_a_first_delivery_takes_the_count_date_back(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    received = await purchase_receipts.record_receipts(
        session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1
    )
    await session.refresh(material)
    assert material.last_stock_take_at is not None

    await purchase_receipts.delete_receipts(session, purchase.id, [r.id for r in received.lines[0].receipts])

    await session.refresh(material)
    assert material.last_stock_take_at is None


async def test_all_four_on_order_consumers_agree(session):
    """One line, ten ordered, four received — everything that reports "on order" says six.

    The subquery behind this was copy-pasted into three modules and reimplemented in a
    fourth. This is the test that notices when one copy comes back.
    """
    from app.models.kitting import ProductKittingMaterial
    from app.services import forecasting, kitting
    from app.services.buildability import get_expected_max_buildable_by_product

    # Above the threshold it would drop out of the forecast entirely, and the last
    # assertion below would never run.
    material = await _material(session, reorder_threshold=1000)
    product = Product(name="Thing", sku="SKU-OO", current_stock=0, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=1))

    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(4), None)], received_at=JAN1)

    assert (await get_on_order_qty_by_material(session))[material.id] == Decimal(6)

    # 4 on hand + 6 still to come, not the 10 originally ordered.
    expected = await get_expected_max_buildable_by_product(session)
    assert expected[product.id] == 10

    session.add(ProductKittingMaterial(product_id=product.id, material_id=material.id, qty_required=1))
    await session.flush()
    capacity = await kitting.get_expected_kitting_capacity_by_product(session)
    assert capacity[product.id] == 10

    forecasts = await forecasting.compute_material_forecasts(session)
    on_order = {f.material_id: f.on_order_qty for f in forecasts}
    assert on_order[material.id] == Decimal(6)


async def test_editing_lines_preserves_receipts_and_line_ids(session):
    """The detail page saves header and lines together, so an unrelated edit must not
    destroy what has already been received against them."""
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(6), None)], received_at=JAN1)

    updated = await replace_purchase_lines(
        purchase.id,
        [
            PurchaseLineInput(
                id=line_id, material_id=material.id, qty=Decimal(12), total_cost=Decimal(120), notes="revised"
            )
        ],
        session,
    )

    assert [line.id for line in updated.lines] == [line_id]
    assert updated.lines[0].received_qty == Decimal(6)
    assert updated.lines[0].outstanding_qty == Decimal(6)
    assert updated.status == PurchaseStatus.partially_received


async def test_editing_a_line_total_reprices_what_already_arrived(session):
    """The invoice usually turns up after the goods, so a cost edit has to flow through."""
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1)
    await session.refresh(material)
    assert Decimal(material.avg_unit_cost) == Decimal(10)

    material_id = material.id
    await replace_purchase_lines(
        purchase.id,
        [PurchaseLineInput(id=line_id, material_id=material_id, qty=Decimal(10), total_cost=Decimal(250))],
        session,
    )

    session.expire_all()
    refreshed = await session.get(Material, material_id)
    assert Decimal(refreshed.avg_unit_cost) == Decimal(25)


async def test_editing_lines_refuses_to_undo_a_delivery(session):
    material = await _material(session)
    other = await _material(session, name="Resin")
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(6), None)], received_at=JAN1)

    def line(**overrides):
        base = dict(id=line_id, material_id=material.id, qty=Decimal(10), total_cost=Decimal(100))
        return PurchaseLineInput(**{**base, **overrides})

    # Dropping the line entirely — the receipt has nowhere to go.
    with pytest.raises(HTTPException) as removed:
        await replace_purchase_lines(
            purchase.id, [PurchaseLineInput(material_id=other.id, qty=Decimal(1), total_cost=Decimal(1))], session
        )
    assert removed.value.status_code == 409

    with pytest.raises(HTTPException) as shrunk:
        await replace_purchase_lines(purchase.id, [line(qty=Decimal(3))], session)
    assert shrunk.value.status_code == 409

    # Would teleport six delivered units from one material to another.
    with pytest.raises(HTTPException) as swapped:
        await replace_purchase_lines(purchase.id, [line(material_id=other.id)], session)
    assert swapped.value.status_code == 409


async def test_deleting_a_received_order_takes_its_stock_with_it(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(10), None)], received_at=JAN1)

    material_id = material.id
    await delete_purchase(purchase.id, session)

    session.expire_all()
    refreshed = await session.get(Material, material_id)
    assert Decimal(refreshed.current_qty) == Decimal(0)


async def test_receive_endpoint_takes_everything_outstanding(session):
    material = await _material(session)
    purchase = await _order(session, material.id, 10, "100")
    line_id = await _line_id(session, purchase)
    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(4), None)], received_at=JAN1)

    material_id = material.id
    completed = await receive_purchase(purchase.id, session)
    assert completed.status == PurchaseStatus.received

    with pytest.raises(HTTPException) as again:
        await receive_purchase(purchase.id, session)
    assert again.value.status_code == 400

    session.expire_all()
    refreshed = await session.get(Material, material_id)
    assert Decimal(refreshed.current_qty) == Decimal(10)


async def test_a_delivery_is_one_batch_and_reverses_as_one(session):
    material_a = await _material(session, name="A")
    material_b = await _material(session, name="B")
    a_id, b_id = material_a.id, material_b.id
    purchase = Purchase(status=PurchaseStatus.ordered)
    purchase.lines = [
        MaterialPurchase(material_id=material_a.id, qty=Decimal(10), total_cost=Decimal(100)),
        MaterialPurchase(material_id=material_b.id, qty=Decimal(20), total_cost=Decimal(200)),
    ]
    session.add(purchase)
    await session.commit()
    reloaded = await purchase_receipts._load_purchase(session, purchase.id)
    ids = [line.id for line in reloaded.lines]

    received = await purchase_receipts.record_receipts(
        session,
        purchase.id,
        [(ids[0], Decimal(5), None), (ids[1], Decimal(5), None)],
        received_at=JAN1,
    )
    batches = {r.batch_id for line in received.lines for r in line.receipts}
    assert len(batches) == 1

    batch_id = batches.pop()
    receipt_ids = await purchase_receipts.receipt_ids_for_batch(session, purchase.id, batch_id)
    reverted = await purchase_receipts.delete_receipts(session, purchase.id, receipt_ids)
    assert reverted.status == PurchaseStatus.ordered

    session.expire_all()
    assert Decimal((await session.get(Material, a_id)).current_qty) == Decimal(0)
    assert Decimal((await session.get(Material, b_id)).current_qty) == Decimal(0)


async def test_a_part_delivered_order_forecasts_only_the_remainder(session):
    """The bug this fixes: a half-received order used to forecast its full quantity,
    counting the delivered half twice — once on the shelf and once still in transit."""
    from app.models.general_settings import GeneralSettings
    from app.services.forecasting import compute_material_forecasts

    session.add(GeneralSettings(id=1))
    material = await _material(session, reorder_threshold=1000)
    purchase = Purchase(
        status=PurchaseStatus.ordered,
        expected_arrival_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
    )
    purchase.lines = [MaterialPurchase(material_id=material.id, qty=Decimal(100), total_cost=Decimal(100))]
    session.add(purchase)
    await session.commit()
    line_id = (await purchase_receipts._load_purchase(session, purchase.id)).lines[0].id

    await purchase_receipts.record_receipts(session, purchase.id, [(line_id, Decimal(40), None)])

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    assert forecasts[material.id].on_order_qty == Decimal(60)
