"""The daily/weekly "items due for counting" notification.

Scheduled exactly like the order summary (services/notification_summary.py): wall-clock hour,
optional day of week, fires once per period. Unlike the summary it says nothing when there is
nothing to say — with zero items due it fires no notification at all, but still records the
attempt so it is not re-evaluated every 15-minute tick for the rest of the hour.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.notification import (
    NotificationCategory,
    NotificationDeliveryMode,
    NotificationSettings,
    NotificationUrgency,
    SummaryFrequency,
)
from app.models.product import Product
from app.services import abc
from app.services.notifications import dispatch_notification, resolve_alerts


def _is_due(now: datetime, settings: NotificationSettings) -> bool:
    """See notification_summary._is_due, which this mirrors for the reminder's own columns."""
    if not settings.stock_take_reminder_enabled:
        return False
    if now.hour < settings.stock_take_reminder_hour_local:
        return False
    if settings.stock_take_reminder_frequency == SummaryFrequency.weekly:
        if settings.stock_take_reminder_day_of_week is None or now.weekday() != settings.stock_take_reminder_day_of_week:
            return False

    last = settings.stock_take_reminder_last_fired_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes
    last_local = last.astimezone()
    return last_local.date() < now.date() or last_local.hour < settings.stock_take_reminder_hour_local


async def due_counts_by_category(session: AsyncSession) -> list[tuple[str, int]]:
    """(label, number due) per category, largest first. Materials and finished stock are
    labelled by their own category; anything uncategorised falls under a generic label."""
    due = await abc.compute_due_for_count(session)
    material_ids = {d.material_id for d in due if d.material_id is not None}
    product_ids = {d.product_id for d in due if d.product_id is not None}
    material_category = (
        {m.id: m.category_name for m in (await session.execute(select(Material).where(Material.id.in_(material_ids)))).scalars()}
        if material_ids
        else {}
    )
    product_category = {}
    if product_ids:
        from sqlalchemy.orm import selectinload

        product_category = {
            p.id: p.product_category_name
            for p in (
                await session.execute(
                    select(Product).options(selectinload(Product.product_category)).where(Product.id.in_(product_ids))
                )
            ).scalars()
        }

    counts: dict[str, int] = {}
    for d in due:
        if d.material_id is not None:
            label = material_category.get(d.material_id) or "Other materials"
        else:
            label = product_category.get(d.product_id) or "Uncategorised products"
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


async def maybe_fire_stock_take_reminder(session: AsyncSession, settings: NotificationSettings) -> None:
    now = datetime.now()
    if not _is_due(now, settings):
        return

    counts = await due_counts_by_category(session)
    fired_at = datetime.now(timezone.utc)
    if not counts:
        settings.stock_take_reminder_last_fired_at = fired_at
        await session.commit()
        return

    total = sum(n for _, n in counts)
    # The previous reminder is stale the moment a new one is written.
    await resolve_alerts(session, category=NotificationCategory.stock_take_due)
    await dispatch_notification(
        session,
        category=NotificationCategory.stock_take_due,
        # Immediate for the same reason as the order summary: it is scheduled, so quiet hours
        # should not swallow it.
        urgency=NotificationUrgency.immediate,
        title=f"{total} item{'s' if total != 1 else ''} due for counting",
        body="\n".join(f"{label}: {n}" for label, n in counts),
        delivery_mode=NotificationDeliveryMode.immediate,
    )
    settings.stock_take_reminder_last_fired_at = fired_at
    await session.commit()
