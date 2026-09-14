from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderLine
from app.models.order_substitution import OrderLineSubstitution
from app.models.variant import ProductVariant
from app.services import allocation

"""Order line substitution — "customer wants a different variant". Always a split (see
OrderLineSubstitution's own docstring): qty units move off an existing line onto a new
one pointed at the replacement variant, and can be undone at any point before the
substituted units ship. Restricted to sibling variants of the *same* product — the
target picker in the UI only ever offers those, and this module enforces it too."""


async def substitute_line(
    session: AsyncSession, line: OrderLine, variant_id: int, qty: int, reason: str | None
) -> OrderLine:
    if line.product_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Line has no product to substitute")

    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
    if variant.product_id != line.product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Substitution target must be a variant of the same product"
        )
    if variant_id == line.variant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Line is already this variation")

    available = line.ordered_qty - line.shipped_qty
    if qty <= 0 or qty > available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot substitute {qty} — only {available} not yet shipped",
        )

    order = await session.get(Order, line.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    await allocation.apply_ordered_qty_change(session, line, line.ordered_qty - qty, allow_zero=True)

    new_line = OrderLine(
        order_id=line.order_id,
        product_id=line.product_id,
        variant_id=variant_id,
        ordered_qty=qty,
        unit_price=line.unit_price,
        currency=line.currency,
        external_line_id=None,
        sku=None,
        needs_mapping=False,
        variation_text=line.variation_text,
    )
    session.add(new_line)
    await session.flush()

    session.add(
        OrderLineSubstitution(
            original_line_id=line.id,
            new_line_id=new_line.id,
            qty=qty,
            reason=reason,
        )
    )

    await allocation.allocate_order(session, order, source="substitution")
    return new_line


async def undo_substitution(session: AsyncSession, substitution: OrderLineSubstitution) -> None:
    if substitution.reverted_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Substitution already undone")

    new_line = await session.get(OrderLine, substitution.new_line_id)
    original_line = await session.get(OrderLine, substitution.original_line_id)
    if new_line is None or original_line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Substitution's lines not found")
    if new_line.shipped_qty > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot undo — some substituted units have already shipped",
        )

    if new_line.allocated_qty > 0:
        await allocation.deallocate_line(session, new_line, new_line.allocated_qty)

    reverted_qty = new_line.ordered_qty
    new_line.ordered_qty = 0
    await allocation.apply_ordered_qty_change(
        session, original_line, original_line.ordered_qty + reverted_qty, allow_zero=True
    )

    substitution.reverted_qty = reverted_qty
    substitution.reverted_at = datetime.now(timezone.utc)

    order = await session.get(Order, original_line.order_id)
    if order is not None:
        await allocation.allocate_order(session, order, source="substitution-undo")
