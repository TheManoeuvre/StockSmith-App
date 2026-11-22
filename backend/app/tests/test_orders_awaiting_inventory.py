"""get_orders_awaiting_inventory's has_bom flag: the signal that separates the routine
"short on stock but buildable" case (has_bom=True) from a genuine blocker — no BOM at all
to ever close the shortfall by building more (has_bom=False). See buildability.py and
notification_alerts.check_order_unfulfillable_alerts, which dispatch these under different
NotificationCategory values."""

from datetime import datetime, timezone

from app.models.material import Material
from app.models.order import Order, OrderFulfilmentMethod, OrderLine, OrderStatus
from app.models.product import Product, ProductBundleItem, ProductMaterial
from app.services.buildability import get_orders_awaiting_inventory


def _short_order(product_id: int) -> Order:
    order = Order(status=OrderStatus.pending, order_placed_at=datetime.now(timezone.utc))
    order.lines = [
        OrderLine(ordered_qty=5, allocated_qty=2, product_id=product_id, needs_mapping=False)
    ]
    return order


async def test_short_line_with_a_bom_is_not_blocked(session):
    product = Product(name="Widget")
    material = Material(name="Filament", category="other", unit="g", current_qty=100, avg_unit_cost=1)
    session.add_all([product, material])
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=1))
    session.add(_short_order(product.id))
    await session.commit()

    [line] = await get_orders_awaiting_inventory(session)
    assert line.has_bom is True


async def test_short_line_with_no_bom_is_blocked(session):
    product = Product(name="No-BOM Widget")
    session.add(product)
    await session.flush()
    session.add(_short_order(product.id))
    await session.commit()

    [line] = await get_orders_awaiting_inventory(session)
    assert line.has_bom is False


async def test_short_bundle_line_is_not_blocked_despite_having_no_bom(session):
    bundle = Product(name="Bundle", is_bundle=True)
    component = Product(name="Component")
    session.add_all([bundle, component])
    await session.flush()
    session.add(ProductBundleItem(bundle_product_id=bundle.id, component_product_id=component.id, qty=1))
    session.add(_short_order(bundle.id))
    await session.commit()

    [line] = await get_orders_awaiting_inventory(session)
    assert line.has_bom is True


async def test_due_date_is_collect_by_for_collect_orders_else_ship_by(session):
    product = Product(name="Widget")
    session.add(product)
    await session.flush()
    ship_by = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    collect_by = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    shipped = _short_order(product.id)
    shipped.ship_by_date = ship_by
    collected = _short_order(product.id)
    collected.fulfilment_method = OrderFulfilmentMethod.collect
    collected.ship_by_date = ship_by
    collected.collect_by = collect_by
    session.add_all([shipped, collected])
    await session.commit()

    lines = await get_orders_awaiting_inventory(session)
    due = {line.order_id: line.due_date.replace(tzinfo=timezone.utc) for line in lines}
    assert due[shipped.id] == ship_by
    assert due[collected.id] == collect_by


async def test_order_shipped_on_the_marketplace_is_listed_apart_not_awaiting(session):
    """Built and shipped but never recorded here: nothing left to build, so it leaves every
    awaiting list and shows up as a store mismatch to reconcile instead."""
    from app.services.buildability import get_orders_awaiting_mapping, get_orders_shipped_on_store
    from app.services.kitting import get_orders_awaiting_packaging

    product = Product(name="Widget")
    session.add(product)
    await session.flush()
    mismatched = _short_order(product.id)
    mismatched.marketplace_shipped_at = datetime.now(timezone.utc)
    unmapped = Order(status=OrderStatus.pending, order_placed_at=datetime.now(timezone.utc))
    unmapped.marketplace_shipped_at = datetime.now(timezone.utc)
    unmapped.lines = [OrderLine(ordered_qty=1, allocated_qty=0, sku="X", needs_mapping=True)]
    normal = _short_order(product.id)
    session.add_all([mismatched, unmapped, normal])
    await session.commit()

    assert [l.order_id for l in await get_orders_awaiting_inventory(session)] == [normal.id]
    assert await get_orders_awaiting_mapping(session) == []
    assert await get_orders_awaiting_packaging(session) == []
    listed = await get_orders_shipped_on_store(session)
    assert {o.order_id for o in listed} == {mismatched.id, unmapped.id}
    assert next(o for o in listed if o.order_id == mismatched.id).item_count == 5


async def test_unmapped_line_is_awaiting_mapping_not_inventory(session):
    from app.services.buildability import get_orders_awaiting_mapping

    order = Order(status=OrderStatus.pending, order_placed_at=datetime.now(timezone.utc))
    order.lines = [OrderLine(ordered_qty=2, allocated_qty=0, sku="MYSTERY-1", needs_mapping=True)]
    cancelled = Order(status=OrderStatus.cancelled, order_placed_at=datetime.now(timezone.utc))
    cancelled.lines = [OrderLine(ordered_qty=1, allocated_qty=0, sku="OLD", needs_mapping=True)]
    session.add_all([order, cancelled])
    await session.commit()

    assert await get_orders_awaiting_inventory(session) == []
    [line] = await get_orders_awaiting_mapping(session)
    assert (line.order_id, line.sku, line.short_by) == (order.id, "MYSTERY-1", 2)
