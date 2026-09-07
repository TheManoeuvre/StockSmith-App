"""list_orders pins orders still awaiting shipment ahead of shipped/cancelled ones,
regardless of when they were placed, so a stale open order can't be pushed onto a later
page by a wall of newer completed orders. Within the awaiting block it's oldest-first (the
order that most needs chasing leads); within the terminal block it's newest-first.
"""

from datetime import datetime, timedelta, timezone

from app.models.order import Order, OrderStatus
from app.routers.orders import list_orders

_BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


async def _order(session, *, days_ago: int, status: OrderStatus) -> int:
    order = Order(status=status, order_placed_at=_BASE - timedelta(days=days_ago))
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


async def test_status_filter_still_applies(session):
    await _order(session, days_ago=1, status=OrderStatus.pending)
    shipped = await _order(session, days_ago=2, status=OrderStatus.shipped)

    page = await list_orders(status_filter=OrderStatus.shipped, limit=50, offset=0, session=session)
    assert [o.id for o in page.items] == [shipped]
    assert page.total == 1
