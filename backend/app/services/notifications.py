"""Core notification service: settings access, the dispatcher every alert goes through,
Pushover delivery, and the digest-flush batch send.

Every notification — immediate or digest — is written to the `notifications` table
unconditionally (unless its type is configured `off`); that table is the in-app log and the
source of truth `GET /api/v1/notifications` reads from. External delivery (Pushover, and
the unread-count signal the Tauri shell polls for a Windows toast) is a side effect on top
of that log, not a replacement for it.
"""

import logging
from datetime import datetime, time, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.models.notification import (
    ALERT_TYPES,
    DEFAULT_IMMEDIATE_ALERT_TYPES,
    Notification,
    NotificationCategory,
    NotificationDeliveryMode,
    NotificationSettings,
    NotificationTypeSettings,
    NotificationUrgency,
)

logger = logging.getLogger("stocksmith.notifications")

_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
_DIGEST_BODY_LINE_CAP = 10


async def get_notification_settings(session: AsyncSession) -> NotificationSettings:
    settings = await session.get(NotificationSettings, 1)
    if settings is None:
        # Should only happen on a DB that predates the seeding migration — fall back to the
        # same safe defaults the seed inserts.
        settings = NotificationSettings(id=1)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def get_type_settings(session: AsyncSession) -> list[NotificationTypeSettings]:
    result = await session.execute(select(NotificationTypeSettings).order_by(NotificationTypeSettings.alert_type))
    rows = list(result.scalars())
    existing = {row.alert_type for row in rows}
    missing = [t for t in ALERT_TYPES if t not in existing]
    if missing:
        # Should only happen on a DB that predates the seeding migration.
        for alert_type in missing:
            row = NotificationTypeSettings(
                alert_type=alert_type,
                enabled=True,
                delivery_mode=(
                    NotificationDeliveryMode.immediate
                    if alert_type in DEFAULT_IMMEDIATE_ALERT_TYPES
                    else NotificationDeliveryMode.digest
                ),
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        rows.sort(key=lambda r: r.alert_type.value)
    return rows


async def get_type_settings_map(session: AsyncSession) -> dict[NotificationCategory, NotificationTypeSettings]:
    return {row.alert_type: row for row in await get_type_settings(session)}


def mask_pushover_key(key: str | None) -> str | None:
    """Last 4 characters only, for GET responses — never round-trip the real key to a client
    that's just displaying current settings."""
    if not key:
        return None
    if len(key) <= 4:
        return "•" * len(key)
    return "•" * (len(key) - 4) + key[-4:]


def looks_masked(value: str) -> bool:
    """True if `value` is (or contains) the bullet-masked placeholder mask_pushover_key
    produces, so a PUT that echoes GET's masked key back unedited doesn't overwrite the
    real stored key with the placeholder."""
    return "•" in value


def _in_quiet_hours(settings: NotificationSettings, *, now: datetime | None = None) -> bool:
    if not settings.quiet_hours_enabled:
        return False
    hour = (now or datetime.now()).hour
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start == end:
        # A zero-width window is a degenerate config, not "always on" — treat as disabled
        # rather than silently blocking every external notification forever.
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


async def send_pushover(user_key: str, title: str, body: str) -> tuple[bool, str | None]:
    """POSTs one message to Pushover. Returns (success, reason) — reason is a human-readable
    failure explanation (bad key vs. network error) on failure, None on success. Never
    raises: a bad key or a flaky network must not crash the caller (an alert-detection loop,
    or the settings router's test-send endpoint)."""
    token = app_settings.pushover_app_api_token
    if not token:
        logger.warning("Pushover is enabled but PUSHOVER_APP_API_TOKEN is not configured — skipping send")
        return False, "Pushover is not configured on this install (missing application token)."

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _PUSHOVER_URL, data={"token": token, "user": user_key, "title": title, "message": body}
            )
    except httpx.HTTPError as exc:
        logger.warning("Pushover send failed (network error): %s", exc)
        return False, f"Network error contacting Pushover: {exc}"

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code == 200 and payload.get("status") == 1:
        return True, None

    errors = payload.get("errors")
    reason = "; ".join(errors) if errors else f"Pushover returned HTTP {response.status_code}"
    logger.warning("Pushover send failed: %s", reason)
    return False, reason


async def _deliver_external(session: AsyncSession, settings: NotificationSettings, title: str, body: str) -> None:
    if settings.pushover_enabled and settings.pushover_user_key:
        await send_pushover(settings.pushover_user_key, title, body)
    # Windows toast delivery is Tauri/Rust-side, not backend — the Rust shell polls
    # GET /api/v1/notifications/unread-count and raises its own OS toast when that count
    # increases. Nothing further to do here; see routers/notifications.py for the contract.


async def dispatch_notification(
    session: AsyncSession,
    *,
    category: NotificationCategory,
    urgency: NotificationUrgency,
    title: str,
    body: str,
    delivery_mode: NotificationDeliveryMode,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
) -> Notification | None:
    """The single entry point every alert (and the daily/weekly order summary) goes
    through. `off` skips everything, including the in-app log. Otherwise the in-app record
    is unconditional; external delivery only happens immediately for `delivery_mode ==
    immediate` — `digest` items sit as unread rows until the next digest flush (see
    flush_digest) batches them into one external send.

    Quiet hours only suppress *external* delivery, and only for non-immediate-urgency
    notifications — the in-app record always stands, and an immediate-urgency notification
    (e.g. the daily/weekly summary, which is dispatched with urgency=immediate specifically
    so it isn't held back) always reaches Pushover/the toast signal regardless of the
    window.
    """
    if delivery_mode == NotificationDeliveryMode.off:
        return None

    notification = Notification(
        category=category,
        urgency=urgency,
        title=title,
        body=body,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    session.add(notification)
    await session.commit()
    await session.refresh(notification)

    if delivery_mode == NotificationDeliveryMode.immediate:
        settings = await get_notification_settings(session)
        if _in_quiet_hours(settings) and urgency != NotificationUrgency.immediate:
            pass  # suppressed; the in-app record above still stands
        else:
            await _deliver_external(session, settings, title, body)

    return notification


async def flush_digest(session: AsyncSession) -> None:
    """Batches every not-yet-sent digest-mode notification into one Pushover message (and
    the same unread-count bump the Windows toast side reads) — "all unread digest-mode
    notifications since last digest send", per the feature spec. Called from
    notification_scheduler's periodic tick.

    Deliberately keyed on `digest_sent_at IS NULL` rather than `read_at IS NULL`: read_at
    means a person opened it in the app, which can happen before or after this flush and
    must not affect whether it gets batched.
    """
    settings = await get_notification_settings(session)
    if _in_quiet_hours(settings):
        # Wait out the quiet window rather than dropping them — the next tick after it ends
        # will pick these back up since digest_sent_at is still NULL.
        return

    result = await session.execute(
        select(Notification)
        .where(Notification.urgency == NotificationUrgency.digest, Notification.digest_sent_at.is_(None))
        .order_by(Notification.created_at)
    )
    pending = list(result.scalars())
    if not pending:
        return

    title = f"StockSmith: {len(pending)} update{'s' if len(pending) != 1 else ''}"
    lines = [f"- {n.title}" for n in pending[:_DIGEST_BODY_LINE_CAP]]
    if len(pending) > _DIGEST_BODY_LINE_CAP:
        lines.append(f"...and {len(pending) - _DIGEST_BODY_LINE_CAP} more")
    body = "\n".join(lines)

    await _deliver_external(session, settings, title, body)

    now = datetime.now(timezone.utc)
    for notification in pending:
        notification.digest_sent_at = now
    await session.commit()


async def get_unread_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(Notification).where(Notification.read_at.is_(None)))
    return result.scalar_one()


async def mark_read(session: AsyncSession, notification_id: int) -> Notification | None:
    notification = await session.get(Notification, notification_id)
    if notification is None:
        return None
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(notification)
    return notification


async def resolve_alerts(
    session: AsyncSession,
    *,
    category: NotificationCategory,
    related_entity_type: str | None = None,
    related_entity_id: int | None = None,
) -> int:
    """Marks unread notifications matching `category` (and, when given, the related entity)
    as read — called when the condition that raised them has cleared on its own (an order
    unblocked, a material forecast back to healthy, a backlog back under threshold), so a
    resolved alert doesn't sit unread waiting for someone to dismiss it by hand."""
    query = select(Notification).where(Notification.category == category, Notification.read_at.is_(None))
    if related_entity_type is not None:
        query = query.where(Notification.related_entity_type == related_entity_type)
    if related_entity_id is not None:
        query = query.where(Notification.related_entity_id == related_entity_id)
    rows = list((await session.execute(query)).scalars())
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    for row in rows:
        row.read_at = now
    await session.commit()
    return len(rows)


async def mark_all_read(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(select(Notification).where(Notification.read_at.is_(None)))
    unread = list(result.scalars())
    for notification in unread:
        notification.read_at = now
    await session.commit()
    return len(unread)


async def list_notifications(
    session: AsyncSession,
    *,
    category: NotificationCategory | None = None,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    query = select(Notification)
    count_query = select(func.count()).select_from(Notification)
    if category is not None:
        query = query.where(Notification.category == category)
        count_query = count_query.where(Notification.category == category)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
        count_query = count_query.where(Notification.read_at.is_(None))

    total = (await session.execute(count_query)).scalar_one()
    query = query.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    items = list((await session.execute(query)).scalars())
    return items, total


def start_of_local_day(now: datetime) -> datetime:
    """Midnight of `now`'s local calendar day, as a UTC-aware datetime — used as the summary
    window's start on the very first fire (see notification_summary.py), since there is no
    daily_summary_last_fired_at to anchor to yet."""
    local_midnight = datetime.combine(now.date(), time.min).astimezone()
    return local_midnight.astimezone(timezone.utc)
