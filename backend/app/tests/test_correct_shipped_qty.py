"""Correcting a shipped line down when fewer units physically left than were recorded
(allocation.correct_shipped_qty): stock comes back, the order stays shipped, ordered_qty is
untouched, and nothing re-allocates or re-ships the missing units."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.order import OrderStatus
from app.models.product import Product
from app.models.product_stock_event import ProductStockEvent
from app.routers.orders import _get_order_with_lines, correct_shipped_line, create_order
from app.schemas.order import DeallocateRequest, OrderCreate, OrderLineInput
from app.services import allocation


async def _shipped_order(session, ordered: int = 2):
    product = Product(name="Widget", current_stock=ordered)
    session.add(product)
    await session.commit()
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=ordered, unit_price=Decimal("5"))]),
        session=session,
    )
    order = await _get_order_with_lines(session, order_read.id)
    await allocation.ship_order(session, order)
    await session.commit()
    return product, order_read.id, order_read.lines[0].id


async def test_correct_shipped_returns_stock_and_keeps_order_shipped(session):
    product, order_id, line_id = await _shipped_order(session)
    await session.refresh(product)
    assert product.current_stock == 0

    result = await correct_shipped_line(line_id, DeallocateRequest(qty=1), session=session)

    line = result.lines[0]
    assert (line.ordered_qty, line.allocated_qty, line.shipped_qty, line.short_shipped_qty) == (2, 1, 1, 1)
    assert result.status == OrderStatus.shipped
    await session.refresh(product)
    assert product.current_stock == 1
    assert product.allocated_qty == 0
    events = (await session.execute(select(ProductStockEvent).where(ProductStockEvent.qty_delta == 1))).scalars().all()
    assert len(events) == 1 and events[0].source_order_line_id == line_id


async def test_corrected_units_are_not_reallocated(session):
    product, order_id, line_id = await _shipped_order(session)
    await correct_shipped_line(line_id, DeallocateRequest(qty=1), session=session)
    order = await _get_order_with_lines(session, order_id)

    await allocation.allocate_order(session, order, source="manual")
    await session.commit()

    await session.refresh(product)
    assert product.allocated_qty == 0
    assert order.status == OrderStatus.shipped


async def test_cannot_correct_more_than_shipped(session):
    _, _, line_id = await _shipped_order(session)
    with pytest.raises(HTTPException) as exc:
        await correct_shipped_line(line_id, DeallocateRequest(qty=3), session=session)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        await correct_shipped_line(line_id, DeallocateRequest(qty=0), session=session)
