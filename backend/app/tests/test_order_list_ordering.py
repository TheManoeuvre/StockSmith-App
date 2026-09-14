"""list_orders pins orders still awaiting shipment ahead of shipped/cancelled ones,
regardless of when they were placed, so a stale open order can't be pushed onto a later
page by a wall of newer completed orders. Within the awaiting block it's soonest-due-first
(the order most urgently needing chasing leads), falling back to oldest-placed-first for a
shared or missing due date; within the terminal block it's newest-first.
"""

from datetime import datetime, timedelta, timezone

from app.models.order import Order, OrderStatus
from app.routers.orders import list_orders

_BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


async def _order(
    session, *, days_ago: int, status: OrderStatus, due_in_days: int | None = None
) -> int:
    order = Order(
        status=status,
        order_placed_at=_BASE - timedelta(days=days_ago),
        ship_by_date=_BASE + timedelta(days=due_in_days) if due_in_days is not None else None,
    )
    session.add(order)
    await session.flush()
    oid = order.id
    await session.commit()
    return oid


async def test_awaiting_orders_sort_ahead_of_terminal_regardless_of_date(session):
    # A very old open order, plus newer shipped/cancelled ones that would bury it under a
    # plain date-desc sort.
    stale_open = await _order(session, days_ago=90, status=OrderStatus.pending)
    recent_open = await _order(session, days_ago=2, status=OrderStatus.allocated)
    newest_shipped = await _order(session, days_ago=0, status=OrderStatus.shipped)
    old_cancelled = await _order(session, days_ago=120, status=OrderStatus.cancelled)

    page = await list_orders(limit=50, offset=0, session=session)
    ids = [o.id for o in page.items]

    # Both awaiting orders come first, oldest-first; terminal orders follow, newest-first.
    assert ids == [stale_open, recent_open, newest_shipped, old_cancelled]
    assert page.total == 4


async def test_awaiting_pin_holds_across_pages(session):
    # One awaiting order older than a page's worth of shipped orders.
    # days_ago 1, 3, 5 → shipped_newest ... shipped_oldest.
    shipped_newest = await _order(session, days_ago=1, status=OrderStatus.shipped)
    shipped_mid = await _order(session, days_ago=3, status=OrderStatus.shipped)
    shipped_oldest = await _order(session, days_ago=5, status=OrderStatus.shipped)
    stale_open = await _order(session, days_ago=365, status=OrderStatus.pending)

    first = await list_orders(limit=1, offset=0, session=session)
    assert [o.id for o in first.items] == [stale_open]

    rest = await list_orders(limit=10, offset=1, session=session)
    # Remaining rows are the shipped ones, newest-first, and the open order never reappears.
    assert [o.id for o in rest.items] == [shipped_newest, shipped_mid, shipped_oldest]


async def test_awaiting_orders_sort_by_soonest_due_date(session):
    # Placed order is irrelevant once a due date is set — the soonest-due order leads even
    # though it was placed most recently.
    due_soon = await _order(session, days_ago=1, status=OrderStatus.pending, due_in_days=1)
    due_later = await _order(session, days_ago=10, status=OrderStatus.allocated, due_in_days=5)
    # No due date at all sorts last, as if it were furthest out — not treated as most urgent.
    no_due_date = await _order(session, days_ago=5, status=OrderStatus.pending)

    page = await list_orders(limit=50, offset=0, session=session)
    assert [o.id for o in page.items] == [due_soon, due_later, no_due_date]


async def test_awaiting_orders_with_same_due_date_fall_back_to_oldest_placed(session):
    older_placed = await _order(session, days_ago=10, status=OrderStatus.pending, due_in_days=3)
    newer_placed = await _order(session, days_ago=1, status=OrderStatus.pending, due_in_days=3)

    page = await list_orders(limit=50, offset=0, session=session)
    assert [o.id for o in page.items] == [older_placed, newer_placed]


async def test_status_filter_still_applies(session):
    await _order(session, days_ago=1, status=OrderStatus.pending)
    shipped = await _order(session, days_ago=2, status=OrderStatus.shipped)

    page = await list_orders(status_filter=OrderStatus.shipped, limit=50, offset=0, session=session)
    assert [o.id for o in page.items] == [shipped]
    assert page.total == 1
