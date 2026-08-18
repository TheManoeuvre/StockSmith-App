from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.product_stock_event import ProductStockEventType
from app.models.stock_adjustment import StockAdjustment, StockAdjustmentMode
from app.models.variant import ProductVariant
from app.services import listing_push
from app.services.stock_events import record_stock_event


async def _get_owner(session: AsyncSession, product_id: int, variant_id: int | None) -> Product | ProductVariant:
    if variant_id is not None:
        owner = await session.get(ProductVariant, variant_id)
        if owner is None or owner.product_id != product_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
        return owner
    owner = await session.get(Product, product_id)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return owner


async def create_stock_adjustment(
    session: AsyncSession,
    product_id: int,
    variant_id: int | None,
    mode: StockAdjustmentMode,
    value: int,
    reason: str,
) -> StockAdjustment:
    owner = await _get_owner(session, product_id, variant_id)

    if mode == StockAdjustmentMode.set:
        qty_delta = value - owner.current_stock
        target_qty = value
    else:
        qty_delta = value
        target_qty = None

    new_stock = owner.current_stock + qty_delta
    if new_stock < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Adjustment would make current_stock negative")
    if new_stock < owner.allocated_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reduce stock below {owner.allocated_qty} unit(s) already allocated to orders",
        )

    owner.current_stock = new_stock
    # Same rule as the materials side (services/costing.py::create_adjustment): a "set" is a
    # physical count and restarts the counting clock, an "adjust" is a known delta and does
    # not. Written onto `owner` rather than the product, so a variant — which holds its own
    # stock and so is counted in its own right — gets its own date.
    if mode == StockAdjustmentMode.set:
        owner.last_stock_take_at = datetime.now(timezone.utc)
    listing_push.enqueue_for_owner(owner)
    adjustment = StockAdjustment(
        product_id=product_id,
        variant_id=variant_id,
        mode=mode,
        qty_delta=qty_delta,
        target_qty=target_qty,
        reason=reason,
    )
    session.add(adjustment)
    await session.flush()
    record_stock_event(
        session,
        product_id=product_id,
        variant_id=variant_id,
        event_type=ProductStockEventType.adjustment,
        qty_delta=qty_delta,
        running_balance=new_stock,
        reason=reason,
        source_adjustment_id=adjustment.id,
    )
    await session.commit()
    await session.refresh(adjustment)
    return adjustment
