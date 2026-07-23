from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.allocation_event import AllocationEvent, AllocationEventType
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant
from app.services.kitting import reconcile_order_kitting
from app.services.shipping_profiles import resolve_shipping_cost_for_platform

"""Core allocation engine: every stock-reservation state change for an order flows
through here. Functions always re-query related rows via explicit select() rather than
touching ORM relationship attributes lazily — AsyncSession relationships raise
MissingGreenlet if accessed without eager-loading, a bug class this project has hit
repeatedly elsewhere, so this module sidesteps it entirely by never relying on lazy
attribute access."""


async def _get_lines(session: AsyncSession, order_id: int) -> list[OrderLine]:
    result = await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))
    return list(result.scalars())


async def _get_stock_owner(session: AsyncSession, line: OrderLine) -> Product | ProductVariant | None:
    if line.variant_id is not None:
        return await session.get(ProductVariant, line.variant_id)
    if line.product_id is not None:
        return await session.get(Product, line.product_id)
    return None


async def _recompute_order_status(session: AsyncSession, order: Order) -> None:
    if order.status == OrderStatus.cancelled:
        return
    lines = await _get_lines(session, order.id)
    if not lines:
        return
    total_ordered = sum(l.ordered_qty for l in lines)
    total_shipped = sum(l.shipped_qty for l in lines)
    if total_shipped >= total_ordered:
        order.status = OrderStatus.shipped
    elif all(l.allocated_qty >= l.ordered_qty for l in lines):
        order.status = OrderStatus.allocated
    else:
        order.status = OrderStatus.pending


async def _allocate_line(session: AsyncSession, line: OrderLine, source: str) -> int:
    """Allocates as much as possible to a single line from current free stock (current_stock
    minus what's already allocated). Returns the qty actually granted."""
    owner = await _get_stock_owner(session, line)
    if owner is None:
        return 0
    need = line.ordered_qty - line.allocated_qty
    if need <= 0:
        return 0
    free = owner.current_stock - owner.allocated_qty
    take = min(need, free)
    if take <= 0:
        return 0
    line.allocated_qty += take
    owner.allocated_qty += take
    event_type = AllocationEventType.auto_allocate if source.startswith("build#") else AllocationEventType.allocate
    session.add(
        AllocationEvent(
            order_line_id=line.id,
            product_id=line.product_id,
            variant_id=line.variant_id,
            event_type=event_type,
            qty=take,
            source=source,
        )
    )
    return take


async def allocate_order(session: AsyncSession, order: Order, source: str = "order-create") -> None:
    """Allocates every unmapped line on the order as far as current free stock allows."""
    lines = await _get_lines(session, order.id)
    for line in lines:
        if line.needs_mapping:
            continue
        await _allocate_line(session, line, source)
    await _recompute_order_status(session, order)
    await reconcile_order_kitting(session, order)


async def auto_allocate_after_build(
    session: AsyncSession, product_id: int, variant_id: int | None, source: str
) -> None:
    """FIFO: grants newly-built free stock to the oldest pending/partial order lines for
    this exact product/variant, oldest order first. Called from create_build in the same
    transaction as the qty_built increment, so build+allocation are atomic."""
    stmt = (
        select(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .where(
            OrderLine.product_id == product_id,
            OrderLine.variant_id == variant_id,
            OrderLine.allocated_qty < OrderLine.ordered_qty,
            OrderLine.needs_mapping.is_(False),
            Order.status.in_([OrderStatus.pending, OrderStatus.allocated]),
        )
        .order_by(Order.order_placed_at, Order.id)
    )
    lines = list((await session.execute(stmt)).scalars())

    touched_order_ids: set[int] = set()
    for line in lines:
        granted = await _allocate_line(session, line, source)
        if granted > 0:
            touched_order_ids.add(line.order_id)

    for order_id in touched_order_ids:
        order = await session.get(Order, order_id)
        if order is not None:
            await _recompute_order_status(session, order)
            await reconcile_order_kitting(session, order)


async def cancel_order(session: AsyncSession, order: Order) -> None:
    if order.status == OrderStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order is already cancelled")

    lines = await _get_lines(session, order.id)
    for line in lines:
        if line.shipped_qty > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot cancel an order with shipped units — process a return instead",
            )

    for line in lines:
        if line.allocated_qty <= 0:
            continue
        owner = await _get_stock_owner(session, line)
        qty = line.allocated_qty
        if owner is not None:
            owner.allocated_qty -= qty
        line.allocated_qty = 0
        session.add(
            AllocationEvent(
                order_line_id=line.id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                event_type=AllocationEventType.deallocate,
                qty=qty,
                source="order-cancel",
            )
        )

    order.status = OrderStatus.cancelled
    order.cancelled_at = datetime.now(timezone.utc)
    # Whatever the sync_issue flag was warning about (see order_sync._reconcile_status —
    # e.g. "Etsy shows this shipped but nothing's allocated") is moot once the order is
    # cancelled locally; nothing left to allocate/ship against it.
    order.sync_issue = None
    await reconcile_order_kitting(session, order)


async def deallocate_line(session: AsyncSession, line: OrderLine, qty: int) -> None:
    """Manual unassign — releases up to `qty` allocated-but-not-yet-shipped units from a
    single line back to free stock, without touching the rest of the order."""
    available = line.allocated_qty - line.shipped_qty
    if qty <= 0 or qty > available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot unassign {qty} — only {available} allocated-but-unshipped",
        )
    owner = await _get_stock_owner(session, line)
    line.allocated_qty -= qty
    if owner is not None:
        owner.allocated_qty -= qty
    session.add(
        AllocationEvent(
            order_line_id=line.id,
            product_id=line.product_id,
            variant_id=line.variant_id,
            event_type=AllocationEventType.deallocate,
            qty=qty,
            source="manual",
        )
    )
    order = await session.get(Order, line.order_id)
    if order is not None:
        await _recompute_order_status(session, order)
        await reconcile_order_kitting(session, order)


async def ship_line(session: AsyncSession, line: OrderLine, qty: int) -> None:
    """The only place current_stock is ever decremented — shipped units and their
    allocation both physically leave the building."""
    available = line.allocated_qty - line.shipped_qty
    if qty <= 0 or qty > available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot ship {qty} — only {available} allocated-but-unshipped",
        )
    owner = await _get_stock_owner(session, line)
    line.shipped_qty += qty
    if owner is not None:
        owner.current_stock -= qty
        owner.allocated_qty -= qty
    session.add(
        AllocationEvent(
            order_line_id=line.id,
            product_id=line.product_id,
            variant_id=line.variant_id,
            event_type=AllocationEventType.ship,
            qty=qty,
            source="manual",
        )
    )


async def ship_order(session: AsyncSession, order: Order) -> None:
    if order.status == OrderStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot ship a cancelled order")

    lines = await _get_lines(session, order.id)
    shippable = [(line, line.allocated_qty - line.shipped_qty) for line in lines]
    shippable = [(line, qty) for line, qty in shippable if qty > 0]
    if not shippable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No allocated units to ship")

    for line, qty in shippable:
        await ship_line(session, line, qty)

    order.shipped_at = datetime.now(timezone.utc)
    await _recompute_order_status(session, order)
    if order.status == OrderStatus.shipped:
        # shipped_qty only ever changes here, via ship_line above — so this is the one
        # place an order can actually become fully shipped, whether that happened via a
        # manual "Ship allocated units" click or a sync's own reconciliation. Either way,
        # any earlier sync_issue (order_sync._reconcile_status — "Etsy shows this shipped
        # but nothing's allocated") no longer applies once we've genuinely shipped it.
        order.sync_issue = None
    await reconcile_order_kitting(session, order)

    # Freeze the shipping profile's cost onto the order exactly once — on whichever
    # ship_order call first ships anything — so a shipped order's cost/profit doesn't
    # drift if the profile's cost changes later. Deliberately at ship time (unlike the
    # build/kitting cost snapshots on OrderLine, which freeze at line-creation time).
    if order.shipping_profile_id is not None and order.shipping_cost_snapshot is None:
        profile = await session.get(ShippingProfile, order.shipping_profile_id)
        if profile is not None:
            order.shipping_cost_snapshot = resolve_shipping_cost_for_platform(profile, order.platform)


async def apply_ordered_qty_change(session: AsyncSession, line: OrderLine, new_ordered_qty: int) -> None:
    """Mirrors Purchase's rule that mutating a line while the order is in an effectful
    state re-fires that effect: lowering ordered_qty below what's already allocated
    deallocates the difference first; raising it just leaves the extra as unallocated
    demand for the next allocate/auto-allocate pass to pick up."""
    if new_ordered_qty <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ordered_qty must be positive")
    if new_ordered_qty < line.shipped_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="ordered_qty cannot be less than what's already shipped"
        )
    if new_ordered_qty < line.allocated_qty:
        await deallocate_line(session, line, line.allocated_qty - new_ordered_qty)
    line.ordered_qty = new_ordered_qty
    order = await session.get(Order, line.order_id)
    if order is not None:
        await _recompute_order_status(session, order)
