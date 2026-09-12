"""get_orders_awaiting_inventory's has_bom flag: the signal that separates the routine
"short on stock but buildable" case (has_bom=True) from a genuine blocker — no BOM at all
to ever close the shortfall by building more (has_bom=False). See buildability.py and
notification_alerts.check_order_unfulfillable_alerts, which dispatch these under different
NotificationCategory values."""

from datetime import datetime, timezone

from app.models.material import Material
from app.models.order import Order, OrderLine, OrderStatus
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
