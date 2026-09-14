"""The daily/weekly shipped-order summary — a periodic notification rather than an
alert triggered by a single event, on the same local-hour-scheduled pattern
backup_scheduler uses for scheduled backups (see `_is_due` there and its docstring).

Reuses the order costing/profit logic verbatim from routers/orders.py rather than
recomputing it — that logic (net profit = revenue - fees - postage - COGS, plus the
cogs_pending/postage_cost_missing "is this figure trustworthy yet" flags) is exactly what
the Orders page already shows per-order, and duplicating it here would be a second place for
the two to drift apart.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notification import NotificationCategory, NotificationDeliveryMode, NotificationSettings, NotificationUrgency, SummaryFrequency
from app.models.order import Order
from app.services.kitting import get_kitting_cogs_by_order
from app.services.notifications import dispatch_notification, start_of_local_day


def _is_due(now: datetime, settings: NotificationSettings) -> bool:
    """Mirrors backup_scheduler._is_due's "reached the hour, haven't already run since it
    came round" shape, with one addition: a weekly cadence also gates on today being the
    configured day-of-week. Comparing calendar dates (not "N hours since last run") is
    deliberate for the same reason as backups — "summarize at 6pm" is a wall-clock
    intention, and a laptop asleep at 6pm should fire shortly after waking, not drift later
    every day.
    """
    if not settings.daily_summary_enabled:
        return False
    if now.hour < settings.daily_summary_hour_local:
        return False
    if settings.daily_summary_frequency == SummaryFrequency.weekly:
        if settings.daily_summary_day_of_week is None or now.weekday() != settings.daily_summary_day_of_week:
            return False

    last = settings.daily_summary_last_fired_at
    if last is None:
        return True
    last_local = last.astimezone()
    return last_local.date() < now.date() or last_local.hour < settings.daily_summary_hour_local


async def maybe_fire_order_summary(session: AsyncSession, settings: NotificationSettings) -> None:
    now = datetime.now()
    if not _is_due(now, settings):
        return

    from app.routers.orders import _compute_net_profit, _cogs_pending, _materials_cogs, _postage_cost_missing

    window_start = settings.daily_summary_last_fired_at or start_of_local_day(now)
    window_end = datetime.now(timezone.utc)

    result = await session.execute(
        select(Order)
        .where(Order.shipped_at.is_not(None), Order.shipped_at > window_start, Order.shipped_at <= window_end)
        .options(selectinload(Order.lines))
    )
    orders = list(result.scalars())

    if not orders:
        settings.daily_summary_last_fired_at = window_end
        await session.commit()
        return

    kitting_cogs_by_order = await get_kitting_cogs_by_order(session, [order.id for order in orders])

    items_shipped = 0
    total_revenue = 0
    net_profit_total = 0
    flagged_order_ids: set[int] = set()

    for order in orders:
        items_shipped += sum(line.shipped_qty for line in order.lines)

        materials_cogs = _materials_cogs(order)
        kitting_cogs = kitting_cogs_by_order.get(order.id)
        profit = _compute_net_profit(order, materials_cogs, kitting_cogs)

        if order.subtotal is not None:
            total_revenue += float(order.subtotal) + float(order.shipping_charged or 0) - float(order.refunded_amount or 0)

        if profit is None or _cogs_pending(order) or _postage_cost_missing(order):
            flagged_order_ids.add(order.id)
        if profit is not None:
            net_profit_total += float(profit)

    order_count = len(orders)
    body_lines = [
        f"{order_count} order{'s' if order_count != 1 else ''} shipped, {items_shipped} item{'s' if items_shipped != 1 else ''}.",
        f"Revenue: {total_revenue:.2f}",
        f"Net profit: {net_profit_total:.2f}",
    ]
    if flagged_order_ids:
        # _compute_net_profit treats a missing materials/kitting cost as £0 rather than
        # refusing to total it — accurate for a fully-costed order, understated for one
        # still waiting on allocation or a shipping profile. Flag it rather than let the
        # figure above pass as more trustworthy than it is.
        n = len(flagged_order_ids)
        body_lines.append(
            f"Net profit may be understated: {n} order{'s' if n != 1 else ''} pending cost sync."
        )

    period = "Weekly" if settings.daily_summary_frequency == SummaryFrequency.weekly else "Daily"
    await dispatch_notification(
        session,
        category=NotificationCategory.daily_summary,
        # Always immediate: the summary is inherently a digest of its own period, and it
        # must reach Pushover/the toast signal even during quiet hours (dispatch_notification
        # only suppresses non-immediate urgency there).
        urgency=NotificationUrgency.immediate,
        title=f"{period} order summary",
        body="\n".join(body_lines),
        delivery_mode=NotificationDeliveryMode.immediate,
    )

    settings.daily_summary_last_fired_at = window_end
    await session.commit()
