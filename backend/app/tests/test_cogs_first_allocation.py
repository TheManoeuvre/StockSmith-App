"""Item 2: COGS is snapshotted at a line's first allocation (not creation), COGS in
net_profit is based on shipped_qty (not ordered_qty), and OrderRead.cogs_pending flags an
order that has a not-yet-costed line so a UI doesn't silently read net_profit as though
that line cost nothing.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.material import Material, MaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine
from app.models.product import Product, ProductMaterial
from app.routers.orders import _cogs_pending, _compute_net_profit, _get_order_with_lines, create_order
from app.schemas.order import OrderCreate, OrderLineInput
from app.services import allocation


async def _costed_product(
    session, current_stock: int, unit_cost: Decimal = Decimal("5"), qty_required: Decimal = Decimal("2")
) -> Product:
    material = Material(name="Filament", category=MaterialCategory.filament, unit=MaterialUnit.g, avg_unit_cost=unit_cost)
    session.add(material)
    await session.flush()
    product = Product(name="Widget", sku="SKU-COST", current_stock=current_stock, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=qty_required))
    await session.commit()
    return product


async def _order_lines(session, order_id: int) -> list[OrderLine]:
    return list((await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars())


async def test_line_has_no_snapshot_until_first_allocation(session):
    product = await _costed_product(session, current_stock=0)

    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=2, unit_price=Decimal("10"))]),
        session=session,
    )

    line = order_read.lines[0]
    assert line.allocated_qty == 0
    assert line.cost_per_unit_snapshot is None
    assert line.kitting_cost_per_unit_snapshot is None
    assert order_read.cogs_pending is True


async def test_snapshot_captured_at_first_allocation(session):
    product = await _costed_product(session, current_stock=10)

    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=2, unit_price=Decimal("10"))]),
        session=session,
    )

    line = order_read.lines[0]
    assert line.allocated_qty == 2
    assert Decimal(line.cost_per_unit_snapshot) == Decimal("10")  # 2 * 5
    assert order_read.cogs_pending is False


async def test_net_profit_uses_shipped_qty_not_ordered_qty(session):
    product = await _costed_product(session, current_stock=10)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=2, unit_price=Decimal("10"))]),
        session=session,
    )
    order = await session.get(Order, order_read.id)
    order.subtotal = Decimal("20")
    order.shipping_charged = Decimal("0")
    await session.commit()

    line = (await _order_lines(session, order.id))[0]
    await allocation.ship_line(session, line, 1)  # ship only 1 of the 2 allocated
    await session.commit()

    order = await _get_order_with_lines(session, order.id)
    net_profit = _compute_net_profit(order)
    # revenue 20 - cogs (1 unit * 10) = 10, not 20 - (2*10) = 0
    assert net_profit == Decimal("10")


async def test_cogs_pending_true_when_a_mapped_line_has_no_snapshot(session):
    product = await _costed_product(session, current_stock=0)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=1, unit_price=Decimal("10"))]),
        session=session,
    )

    order = await _get_order_with_lines(session, order_read.id)
    assert _cogs_pending(order) is True
