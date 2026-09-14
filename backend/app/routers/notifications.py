from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.notification import ALERT_TYPES, NotificationCategory, NotificationTypeSettings
from app.schemas.notification import (
    NotificationPage,
    NotificationRead,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    NotificationTypeSettingRead,
    PushoverTestResult,
    UnreadCount,
)
from app.services import notifications

settings_router = APIRouter(prefix="/settings/notifications", tags=["notifications"], dependencies=[Depends(require_auth)])
router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_auth)])


def _settings_read(settings_row, type_rows: list[NotificationTypeSettings]) -> NotificationSettingsRead:
    return NotificationSettingsRead(
        windows_notifications_enabled=settings_row.windows_notifications_enabled,
        pushover_enabled=settings_row.pushover_enabled,
        pushover_user_key_masked=notifications.mask_pushover_key(settings_row.pushover_user_key),
        quiet_hours_enabled=settings_row.quiet_hours_enabled,
        quiet_hours_start=settings_row.quiet_hours_start,
        quiet_hours_end=settings_row.quiet_hours_end,
        daily_summary_enabled=settings_row.daily_summary_enabled,
        daily_summary_frequency=settings_row.daily_summary_frequency,
        daily_summary_hour_local=settings_row.daily_summary_hour_local,
        daily_summary_day_of_week=settings_row.daily_summary_day_of_week,
        pending_order_threshold=settings_row.pending_order_threshold,
        alert_types=[NotificationTypeSettingRead.model_validate(row) for row in type_rows],
    )


def _resolve_pushover_key(payload_key: str | None, current_key: str | None) -> str | None:
    """See NotificationSettingsUpdate.pushover_user_key's docstring: this is a full-object
    PUT, and GET never returns the real key (only a masked last-4 preview) — so a client
    that round-trips the settings object unedited must not overwrite the stored key with
    that masked placeholder."""
    if payload_key is None:
        return current_key
    if notifications.looks_masked(payload_key):
        return current_key
    stripped = payload_key.strip()
    return stripped or None


@settings_router.get("", response_model=NotificationSettingsRead)
async def get_notification_settings(session: AsyncSession = Depends(get_db)) -> NotificationSettingsRead:
    settings_row = await notifications.get_notification_settings(session)
    type_rows = await notifications.get_type_settings(session)
    return _settings_read(settings_row, type_rows)


@settings_router.put("", response_model=NotificationSettingsRead)
async def update_notification_settings(
    payload: NotificationSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> NotificationSettingsRead:
    settings_row = await notifications.get_notification_settings(session)
    type_rows_by_type = {row.alert_type: row for row in await notifications.get_type_settings(session)}

    submitted_types = {row.alert_type for row in payload.alert_types}
    missing = set(ALERT_TYPES) - submitted_types
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Missing settings for alert type(s): {', '.join(sorted(t.value for t in missing))}",
        )

    settings_row.windows_notifications_enabled = payload.windows_notifications_enabled
    settings_row.pushover_enabled = payload.pushover_enabled
    settings_row.pushover_user_key = _resolve_pushover_key(payload.pushover_user_key, settings_row.pushover_user_key)
    settings_row.quiet_hours_enabled = payload.quiet_hours_enabled
    settings_row.quiet_hours_start = payload.quiet_hours_start
    settings_row.quiet_hours_end = payload.quiet_hours_end
    settings_row.daily_summary_enabled = payload.daily_summary_enabled
    settings_row.daily_summary_frequency = payload.daily_summary_frequency
    settings_row.daily_summary_hour_local = payload.daily_summary_hour_local
    settings_row.daily_summary_day_of_week = (
        payload.daily_summary_day_of_week if payload.daily_summary_frequency.value == "weekly" else None
    )
    settings_row.pending_order_threshold = payload.pending_order_threshold

    for type_payload in payload.alert_types:
        row = type_rows_by_type[type_payload.alert_type]
        row.enabled = type_payload.enabled
        row.delivery_mode = type_payload.delivery_mode

    await session.commit()
    await session.refresh(settings_row)
    type_rows = await notifications.get_type_settings(session)
    return _settings_read(settings_row, type_rows)


@settings_router.post("/pushover/test", response_model=PushoverTestResult)
async def test_pushover(session: AsyncSession = Depends(get_db)) -> PushoverTestResult:
    settings_row = await notifications.get_notification_settings(session)
    if not settings_row.pushover_user_key:
        return PushoverTestResult(success=False, reason="No Pushover user key is saved yet.")
    success, reason = await notifications.send_pushover(
        settings_row.pushover_user_key, "StockSmith test notification", "Pushover is set up correctly."
    )
    return PushoverTestResult(success=success, reason=reason)


@settings_router.delete("/pushover", status_code=status.HTTP_204_NO_CONTENT)
async def clear_pushover(session: AsyncSession = Depends(get_db)) -> None:
    settings_row = await notifications.get_notification_settings(session)
    settings_row.pushover_user_key = None
    settings_row.pushover_enabled = False
    await session.commit()


@router.get("", response_model=NotificationPage)
async def list_notifications(
    category: NotificationCategory | None = Query(default=None),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> NotificationPage:
    items, total = await notifications.list_notifications(
        session, category=category, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationPage(items=[NotificationRead.model_validate(item) for item in items], total=total)


@router.get("/unread-count", response_model=UnreadCount)
async def unread_count(session: AsyncSession = Depends(get_db)) -> UnreadCount:
    """Polled by the Tauri shell to know when to raise a native Windows toast for a new
    immediate notification — the backend has no OS-level toast capability of its own, so
    this count (rising since the last poll) is the entire contract between the two sides.
    Cheap: one COUNT(*) query with an index-friendly predicate."""
    return UnreadCount(count=await notifications.get_unread_count(session))


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(notification_id: int, session: AsyncSession = Depends(get_db)) -> NotificationRead:
    notification = await notifications.mark_read(session, notification_id)
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return NotificationRead.model_validate(notification)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_notifications_read(session: AsyncSession = Depends(get_db)) -> None:
    await notifications.mark_all_read(session)
