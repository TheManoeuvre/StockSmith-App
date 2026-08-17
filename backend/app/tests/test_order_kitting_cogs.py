"""Packaging cost is an ORDER-level figure read off the OrderKittingAllocation ledger, not a
per-unit rate on each order line.

The bug this replaces: kitting cost used to be snapshotted per unit and multiplied by
shipped_qty, while auto_apply_multiunit_kitting_override had already capped the order's
physical packaging consumption at one. A 3-unit order consumed one box and was charged for
three. See kitting.get_kitting_cogs_by_order.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.kitting import OrderKittingAllocation, OrderKittingOverride, ProductKittingMaterial
from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine
from app.models.product import Product, ProductMaterial
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.routers.orders import _get_order_with_lines, _serialize_one, create_order
from app.schemas.order import OrderCreate, OrderLineInput
from app.services import allocation, listing_push
from app.services.costing import recompute_material
from app.services.kitting import get_kitting_cogs_by_order, get_order_kitting_summary, reconcile_order_kitting


_STOCKED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    # Same re-patch as test_kitting_multiunit_override — conftest's `pushes` stubs
    # enqueue_for_material non-async, but reconcile_order_kitting awaits it whenever a
    # kitting reservation actually changes. Depends on `pushes` for ordering.
    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _box(session, unit_cost: Decimal = Decimal("2.00"), qty: int = 100) -> Material:
    """A packaging material stocked via a real received purchase. current_qty/avg_unit_cost
    are derived columns — costing.recompute_material replays purchase history to rebuild
    them, and shipping consumes kitting through that path, so setting them directly would
    be wiped out (to a CHECK-violating negative) the moment an order ships."""
    material = Material(name="Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each)
    session.add(material)
    await session.flush()
    # Dated well in the past so it always replays before the consumption adjustments the
    # tests generate — _MATERIAL_HISTORY_SQL orders purchases and adjustments together, and
    # a purchase landing after an adjustment shifts the weighted average.
    purchase = Purchase(status=PurchaseStatus.received, received_at=_STOCKED_AT)
    purchase.lines = [MaterialPurchase(material_id=material.id, qty=Decimal(qty), total_cost=unit_cost * qty)]
    session.add(purchase)
    await session.commit()
    await recompute_material(session, material.id)
    await session.commit()
    return material


async def _product(session, box: Material, sku: str = "SKU-K", build_cost: Decimal | None = None) -> Product:
    """A product with a kitting BOM of one box per unit, and optionally a build BOM."""
    product = Product(name=f"Product {sku}", sku=sku, current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=1))
    if build_cost is not None:
        filament = Material(
            name=f"Filament {sku}",
            category=LegacyMaterialCategory.filament,
            unit=MaterialUnit.g,
            avg_unit_cost=build_cost,
        )
        session.add(filament)
        await session.flush()
        session.add(ProductMaterial(product_id=product.id, material_id=filament.id, qty_required=1))
    await session.commit()
    return product


async def _ship_all(session, order_id: int) -> None:
    order = await session.get(Order, order_id)
    await allocation.ship_order(session, order)
    await session.commit()


async def _make_order(session, product: Product, qty: int, unit_price: str = "10.00") -> Order:
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=qty, unit_price=Decimal(unit_price))]),
        session=session,
    )
    order = await session.get(Order, order_read.id)
    order.subtotal = Decimal(unit_price) * qty
    order.shipping_charged = Decimal("0")
    await session.commit()
    return order


async def test_multiunit_order_charges_one_box_not_n(session):
    """The regression. Three units, one box consumed (the auto multi-unit override), so
    kitting COGS is the cost of one box — not three."""
    box = await _box(session, unit_cost=Decimal("2.00"))
    product = await _product(session, box)
    order = await _make_order(session, product, qty=3)

    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    assert Decimal(override.qty_required) == Decimal(1)  # the auto multi-unit default

    await _ship_all(session, order.id)

    ledger = (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    assert Decimal(ledger.consumed_qty) == Decimal(1)

    cogs = await get_kitting_cogs_by_order(session, [order.id])
    assert cogs[order.id] == Decimal("2.00")

    read = await _serialize_one(session, await _get_order_with_lines(session, order.id))
    assert Decimal(read.kitting_cogs) == Decimal("2.00")
    # revenue 30 - kitting 2 (no build BOM, so no materials COGS)
    assert Decimal(read.net_profit) == Decimal("28.00")


async def test_kitting_cogs_absent_before_ship(session):
    """Allocated but unshipped: packaging is reserved, not consumed, so there's no cost yet —
    the same "cost isn't real until units leave the building" rule build COGS follows."""
    box = await _box(session)
    product = await _product(session, box)
    order = await _make_order(session, product, qty=2)

    assert await get_kitting_cogs_by_order(session, [order.id]) == {}

    read = await _serialize_one(session, await _get_order_with_lines(session, order.id))
    assert read.kitting_cogs is None
    assert read.net_profit == Decimal("20")  # still computable


async def test_unit_cost_frozen_at_first_consumption(session):
    """Ship half, re-cost the material upward, ship the rest: both halves are valued at the
    cost frozen when the material was first consumed for this order."""
    box = await _box(session, unit_cost=Decimal("2.00"))
    product = Product(name="Product", sku="SKU-FREEZE", current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    # 1 box per unit with NO order-level override, so shipping each unit consumes another box
    # and a second consumption actually happens.
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=1))
    await session.commit()

    order = await _make_order(session, product, qty=2)
    await session.execute(
        OrderKittingOverride.__table__.delete().where(OrderKittingOverride.order_id == order.id)
    )
    await session.commit()

    lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())
    await allocation.ship_line(session, lines[0], 1)
    await reconcile_order_kitting(session, await session.get(Order, order.id))
    await session.commit()

    ledger = (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    assert Decimal(ledger.unit_cost_snapshot) == Decimal("2.00")

    # Re-stock at a much higher price so the weighted average moves — via a real purchase,
    # since assigning avg_unit_cost directly gets replayed away by recompute_material.
    restock = Purchase(status=PurchaseStatus.received, received_at=datetime.now(timezone.utc) + timedelta(days=365))
    restock.lines = [MaterialPurchase(material_id=box.id, qty=Decimal(100), total_cost=Decimal("1000"))]
    session.add(restock)
    await session.commit()
    await recompute_material(session, box.id)
    await session.commit()
    assert Decimal(box.avg_unit_cost) > Decimal("2.00")

    line = (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalar_one()
    await allocation.ship_line(session, line, 1)
    await reconcile_order_kitting(session, await session.get(Order, order.id))
    await session.commit()

    await session.refresh(ledger)
    assert Decimal(ledger.consumed_qty) == Decimal(2)
    assert Decimal(ledger.unit_cost_snapshot) == Decimal("2.00")  # not re-frozen at 9.00

    cogs = await get_kitting_cogs_by_order(session, [order.id])
    assert cogs[order.id] == Decimal("4.00")  # 2 boxes at the ORIGINAL 2.00


async def test_unit_cost_falls_back_to_live_avg_when_snapshot_null(session):
    """Ledger rows written before unit_cost_snapshot existed have NULL there and read
    through to the material's current avg_unit_cost."""
    box = await _box(session, unit_cost=Decimal("2.00"))
    product = await _product(session, box)
    order = await _make_order(session, product, qty=1)
    await _ship_all(session, order.id)

    ledger = (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    ledger.unit_cost_snapshot = None
    box.avg_unit_cost = Decimal("7.50")
    await session.commit()

    cogs = await get_kitting_cogs_by_order(session, [order.id])
    assert cogs[order.id] == Decimal("7.50")


async def test_lowering_override_after_ship_does_not_lower_cogs(session):
    """Consumption is monotonic — reconcile only ever applies a positive delta. Reading the
    ledger (rather than recomputing the requirement) is what keeps a post-ship override edit
    from retroactively un-charging packaging that physically left the building."""
    box = await _box(session, unit_cost=Decimal("2.00"))
    product = await _product(session, box)
    order = await _make_order(session, product, qty=3)

    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    override.qty_required = Decimal(2)
    await session.commit()
    await reconcile_order_kitting(session, await session.get(Order, order.id))
    await session.commit()

    await _ship_all(session, order.id)
    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("4.00")

    override.qty_required = Decimal(1)
    await session.commit()
    await reconcile_order_kitting(session, await session.get(Order, order.id))
    await session.commit()

    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("4.00")


async def test_kitting_cogs_bulk_covers_multiple_orders_in_one_call(session):
    """list_orders pages up to 200 orders and fetches all their kitting COGS in one query."""
    box = await _box(session, unit_cost=Decimal("2.00"), qty=500)
    product = await _product(session, box)
    product.current_stock = 100
    await session.commit()

    orders = []
    for qty in (1, 2, 3):
        order = await _make_order(session, product, qty=qty)
        await _ship_all(session, order.id)
        orders.append(order)

    cogs = await get_kitting_cogs_by_order(session, [o.id for o in orders])
    # qty=1 needs no override (one unit, one box); qty=2 and qty=3 each get the auto
    # override capping them at one box for the whole order.
    assert cogs == {orders[0].id: Decimal("2.00"), orders[1].id: Decimal("2.00"), orders[2].id: Decimal("2.00")}
    assert await get_kitting_cogs_by_order(session, []) == {}


async def test_costs_that_accumulate_float_error_stay_exact(session):
    """Regression: summing in SQL rather than Decimal produced 0.5634319999999999.

    Order 20 on real data: a 0.552442 box and a 0.01099 label. Those two are exactly
    representable enough individually that a str() conversion looks like it works, but
    SQLite adds them as floats and the error appears in the total — which then reached
    net_profit and disagreed with get_order_kitting_summary's Decimal arithmetic.
    """
    box = await _box(session, unit_cost=Decimal("0.552442"))
    label = Material(name="Label", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each)
    session.add(label)
    await session.flush()
    purchase = Purchase(status=PurchaseStatus.received, received_at=_STOCKED_AT)
    purchase.lines = [MaterialPurchase(material_id=label.id, qty=Decimal(100), total_cost=Decimal("1.099"))]
    session.add(purchase)
    await session.commit()
    await recompute_material(session, label.id)
    await session.commit()

    product = Product(name="Product", sku="SKU-FLOAT", current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=1))
    session.add(ProductKittingMaterial(product_id=product.id, material_id=label.id, qty_required=1))
    await session.commit()

    order = await _make_order(session, product, qty=1)
    await _ship_all(session, order.id)

    cogs = await get_kitting_cogs_by_order(session, [order.id])
    assert cogs[order.id] == Decimal("0.563432")
    assert str(cogs[order.id]) == "0.563432"

    # The two code paths must agree — they are shown side by side in the UI.
    summary = await get_order_kitting_summary(session, order.id)
    assert summary.consumed_cost_total == cogs[order.id]


async def test_net_profit_splits_materials_and_kitting(session):
    """The line's Cost is build BOM only; packaging arrives separately as kitting_cogs."""
    box = await _box(session, unit_cost=Decimal("2.00"))
    product = await _product(session, box, sku="SKU-SPLIT", build_cost=Decimal("5.00"))
    order = await _make_order(session, product, qty=1, unit_price="30.00")
    await _ship_all(session, order.id)

    read = await _serialize_one(session, await _get_order_with_lines(session, order.id))
    assert Decimal(read.materials_cogs) == Decimal("5.00")
    assert Decimal(read.kitting_cogs) == Decimal("2.00")
    assert Decimal(read.lines[0].cost_per_unit_snapshot) == Decimal("5.00")  # no packaging in here
    assert Decimal(read.net_profit) == Decimal("23.00")
