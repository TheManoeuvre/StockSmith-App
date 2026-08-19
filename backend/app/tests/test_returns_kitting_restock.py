"""Cancelling a shipped order restocks the packaging it ACTUALLY consumed, once for the
whole order, off the OrderKittingAllocation ledger.

Previously returns.py recomputed the per-unit kitting BOM x shipped_qty per line, which
over-restocked twice over: it ignored the order-level override (returning N boxes for an
N-unit order that consumed one) and it ran once per line (two lines sharing a box returned
it twice).
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.kitting import OrderKittingAllocation, ProductKittingMaterial
from app.models.material import Material, MaterialAdjustment, MaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine
from app.models.order_return import ReturnDisposition
from app.models.product import Product
from app.routers.orders import create_order
from app.schemas.order import OrderCreate, OrderLineInput
from app.schemas.order_return import LineCancellationDecision
from app.services import allocation, listing_push, returns
from app.services.costing import recompute_material
from .conftest import received_purchase

_STOCKED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)
_BOX_QTY = Decimal(100)


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _box(session) -> Material:
    box = Material(name="Box", category=MaterialCategory.packaging, unit=MaterialUnit.each)
    session.add(box)
    await session.flush()
    await received_purchase(session, box.id, _BOX_QTY, Decimal("200"), received_at=_STOCKED_AT)
    await session.commit()
    await recompute_material(session, box.id)
    await session.commit()
    return box


async def _product(session, box: Material, sku: str) -> Product:
    product = Product(name=f"Product {sku}", sku=sku, current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=1))
    await session.commit()
    return product


async def _shipped_two_line_order(session, box: Material) -> Order:
    """Two lines sharing one box — the auto multi-unit override caps the order at 1."""
    a = await _product(session, box, "SKU-A")
    b = await _product(session, box, "SKU-B")
    order_read = await create_order(
        OrderCreate(
            lines=[
                OrderLineInput(product_id=a.id, ordered_qty=1, unit_price=Decimal("10")),
                OrderLineInput(product_id=b.id, ordered_qty=1, unit_price=Decimal("10")),
            ]
        ),
        session=session,
    )
    order = await session.get(Order, order_read.id)
    await allocation.ship_order(session, order)
    await session.commit()
    return order


async def _restock_adjustments(session, box: Material) -> list[MaterialAdjustment]:
    return list(
        (
            await session.execute(
                select(MaterialAdjustment).where(
                    MaterialAdjustment.material_id == box.id, MaterialAdjustment.qty_delta > 0
                )
            )
        ).scalars()
    )


async def _decisions(session, order: Order, kitting: ReturnDisposition) -> list[LineCancellationDecision]:
    lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())
    return [
        LineCancellationDecision(
            order_line_id=line.id,
            product_disposition=ReturnDisposition.return_to_stock,
            kitting_disposition=kitting,
        )
        for line in lines
    ]


async def test_multiline_order_restocks_shared_box_once(session):
    box = await _box(session)
    order = await _shipped_two_line_order(session, box)

    ledger = (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    assert Decimal(ledger.consumed_qty) == Decimal(1)
    await session.refresh(box)
    assert Decimal(box.current_qty) == _BOX_QTY - 1

    order = await session.get(Order, order.id)
    await returns.process_cancellation(session, order, await _decisions(session, order, ReturnDisposition.return_to_stock), None)
    await session.commit()

    restocks = await _restock_adjustments(session, box)
    assert len(restocks) == 1  # one box back, not one per line
    assert Decimal(restocks[0].qty_delta) == Decimal(1)
    await session.refresh(box)
    assert Decimal(box.current_qty) == _BOX_QTY

    # consumed_qty stays put — it's the monotonic record OrderRead.kitting_cogs reads, so a
    # cancelled order still shows what it cost to fulfil.
    await session.refresh(ledger)
    assert Decimal(ledger.consumed_qty) == Decimal(1)


async def test_scrap_disposition_restocks_nothing(session):
    box = await _box(session)
    order = await _shipped_two_line_order(session, box)

    order = await session.get(Order, order.id)
    await returns.process_cancellation(session, order, await _decisions(session, order, ReturnDisposition.scrap), None)
    await session.commit()

    assert await _restock_adjustments(session, box) == []
    await session.refresh(box)
    assert Decimal(box.current_qty) == _BOX_QTY - 1


async def test_empty_ledger_restocks_nothing(session):
    """A shipped order whose ledger drifted (confirmed to happen on migrated data) restocks
    nothing rather than inventing a quantity from the per-unit BOM."""
    box = await _box(session)
    order = await _shipped_two_line_order(session, box)

    await session.execute(
        OrderKittingAllocation.__table__.delete().where(OrderKittingAllocation.order_id == order.id)
    )
    await session.commit()

    order = await session.get(Order, order.id)
    await returns.process_cancellation(session, order, await _decisions(session, order, ReturnDisposition.return_to_stock), None)
    await session.commit()

    assert await _restock_adjustments(session, box) == []
