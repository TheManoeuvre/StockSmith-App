from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_stock_event import ProductStockEvent, ProductStockEventType


def record_stock_event(
    session: AsyncSession,
    *,
    product_id: int,
    variant_id: int | None,
    event_type: ProductStockEventType,
    qty_delta: int,
    running_balance: int,
    reason: str | None = None,
    source_build_id: int | None = None,
    source_adjustment_id: int | None = None,
    source_order_line_id: int | None = None,
) -> ProductStockEvent:
    """Appends one row to the unified "Stock" history ledger — every build (success or
    failed), stock adjustment, and order fulfillment adds exactly one row here, in
    addition to whatever type-specific row it already writes (Build, StockAdjustment,
    AllocationEvent). Callers pass running_balance as the owner's current_stock *after*
    this event, denormalized at write time (see ProductStockEvent's own docstring for why
    this isn't event-sourced/replayed like Material.current_qty)."""
    event = ProductStockEvent(
        product_id=product_id,
        variant_id=variant_id,
        event_type=event_type,
        qty_delta=qty_delta,
        running_balance=running_balance,
        reason=reason,
        source_build_id=source_build_id,
        source_adjustment_id=source_adjustment_id,
        source_order_line_id=source_order_line_id,
    )
    session.add(event)
    return event
