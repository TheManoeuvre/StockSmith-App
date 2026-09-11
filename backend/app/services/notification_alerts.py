"""Alert detection for the 9 configurable notification types.

Two call shapes, matching how each signal actually arrives:

- Reactive hooks: `raise_marketplace_sync_failure_alert` (called from sync_scheduler on a
  PlatformAuthError/PlatformRateLimitError) and `raise_backup_failed_alert` /
  `raise_secondary_backup_unreachable_alert` (called from services/backup.py on its
  existing failure paths). These fire directly off an event that already happened —
  there's nothing to poll.
- Periodic checks, all named `check_*`, called every tick from notification_scheduler:
  material forecast status, orders awaiting inventory, the pending-order-count threshold,
  and marketplace API usage. These recompute a live condition and must not re-alert every
  tick while it stays true — see NotificationAlertState and the dedup helpers below.

Every function here is a no-op if the corresponding NotificationTypeSettings row is
disabled, so a poll against a fully-disabled feature costs one query and nothing else.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.notification import NotificationAlertState, NotificationCategory, NotificationDeliveryMode, NotificationUrgency
from app.models.order import Order, OrderStatus
from app.services import platform_api_usage
from app.services.buildability import get_orders_awaiting_inventory
from app.services.forecasting import compute_material_forecasts
from app.services.notifications import dispatch_notification, get_notification_settings, get_type_settings_map

logger = logging.getLogger("stocksmith.notification_alerts")

_MATERIAL_FORECAST_KEY = "material_forecast_status"
_ORDER_UNFULFILLABLE_KEY = "order_unfulfillable"
_PENDING_ORDER_THRESHOLD_KEY = "pending_order_threshold"
_API_SOFT_LIMIT_KEY = "marketplace_api_soft_limit"
_API_HARD_LIMIT_KEY = "marketplace_api_hard_limit"


async def _get_alert_state(session: AsyncSession, alert_key: str, entity_key: str) -> NotificationAlertState | None:
    result = await session.execute(
        select(NotificationAlertState).where(
            NotificationAlertState.alert_key == alert_key, NotificationAlertState.entity_key == entity_key
        )
    )
    return result.scalar_one_or_none()


async def _load_alert_state_map(session: AsyncSession, alert_key: str) -> dict[str, NotificationAlertState]:
    result = await session.execute(select(NotificationAlertState).where(NotificationAlertState.alert_key == alert_key))
    return {row.entity_key: row for row in result.scalars()}


async def _set_alert_state(session: AsyncSession, alert_key: str, entity_key: str, status: str) -> None:
    row = await _get_alert_state(session, alert_key, entity_key)
    if row is None:
        session.add(NotificationAlertState(alert_key=alert_key, entity_key=entity_key, last_status=status))
    else:
        row.last_status = status
    await session.commit()


async def _clear_alert_state(session: AsyncSession, alert_key: str, entity_key: str) -> None:
    row = await _get_alert_state(session, alert_key, entity_key)
    if row is not None:
        await session.delete(row)
        await session.commit()


# --- Reactive hooks -------------------------------------------------------------------


async def raise_marketplace_sync_failure_alert(session: AsyncSession, platform: ListingPlatform, detail: str) -> None:
    """Called from sync_scheduler._tick on a PlatformAuthError or PlatformRateLimitError.
    Not deduplicated — sync_scheduler already only calls this once per failed tick, and a
    connection stuck failing every cycle is exactly the case where repeated alerts are the
    point (auto-sync disables itself after 3 consecutive failures; until then, every one is
    worth surfacing)."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.marketplace_sync_failure)
    if config is None or not config.enabled:
        return
    await dispatch_notification(
        session,
        category=NotificationCategory.marketplace_sync_failure,
        urgency=NotificationUrgency.immediate,
        title=f"{platform.value.capitalize()} sync failed",
        body=detail,
        delivery_mode=config.delivery_mode,
        related_entity_type="platform",
    )


async def raise_backup_failed_alert(session: AsyncSession, error: str) -> None:
    """Called from services/backup.run_backup's own failure path, using the session it
    already holds — see BackupSettings.last_run_status/last_run_error, which this mirrors
    into a notification."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.backup_failed)
    if config is None or not config.enabled:
        return
    await dispatch_notification(
        session,
        category=NotificationCategory.backup_failed,
        urgency=NotificationUrgency.immediate,
        title="Scheduled backup failed",
        body=error,
        delivery_mode=config.delivery_mode,
    )


async def raise_secondary_backup_unreachable_alert(session: AsyncSession, error: str) -> None:
    """Called from services/backup.run_backup when copying to secondary_dir fails — see
    BackupSettings.secondary_dir_last_error."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.secondary_backup_unreachable)
    if config is None or not config.enabled:
        return
    await dispatch_notification(
        session,
        category=NotificationCategory.secondary_backup_unreachable,
        urgency=NotificationUrgency.digest,
        title="Secondary backup location unreachable",
        body=error,
        delivery_mode=config.delivery_mode,
    )


# --- Periodic checks -------------------------------------------------------------------


async def check_material_forecast_alerts(session: AsyncSession) -> None:
    """Fires only on a material's forecast status *transitioning* into critical/warning —
    forecasting.compute_material_forecasts recomputes fresh from live data on every call and
    has no persisted "last status" of its own, so NotificationAlertState is what remembers
    what a material's status was on the previous poll."""
    type_settings = await get_type_settings_map(session)
    critical_config = type_settings.get(NotificationCategory.material_forecast_critical)
    warning_config = type_settings.get(NotificationCategory.material_forecast_warning)
    critical_on = critical_config is not None and critical_config.enabled
    warning_on = warning_config is not None and warning_config.enabled
    if not critical_on and not warning_on:
        return

    forecasts = await compute_material_forecasts(session)
    previous = await _load_alert_state_map(session, _MATERIAL_FORECAST_KEY)
    seen_keys: set[str] = set()

    for forecast in forecasts:
        key = str(forecast.material_id)
        seen_keys.add(key)
        prior_row = previous.get(key)
        prior_status = prior_row.last_status if prior_row is not None else None

        if forecast.status != prior_status:
            if forecast.status == "critical" and critical_on:
                await dispatch_notification(
                    session,
                    category=NotificationCategory.material_forecast_critical,
                    urgency=NotificationUrgency.immediate,
                    title=f"{forecast.name} is critically low",
                    body=(
                        f"{forecast.weeks_of_supply:.1f} weeks of supply remaining"
                        if forecast.weeks_of_supply is not None
                        else "Stock has dropped into the critical range."
                    ),
                    delivery_mode=critical_config.delivery_mode,
                    related_entity_type="material",
                    related_entity_id=forecast.material_id,
                )
            elif forecast.status == "warning" and warning_on:
                await dispatch_notification(
                    session,
                    category=NotificationCategory.material_forecast_warning,
                    urgency=NotificationUrgency.digest,
                    title=f"{forecast.name} is running low",
                    body=(
                        f"{forecast.weeks_of_supply:.1f} weeks of supply remaining"
                        if forecast.weeks_of_supply is not None
                        else "Stock has dropped into the warning range."
                    ),
                    delivery_mode=warning_config.delivery_mode,
                    related_entity_type="material",
                    related_entity_id=forecast.material_id,
                )
            await _set_alert_state(session, _MATERIAL_FORECAST_KEY, key, forecast.status)

    # Materials no longer forecast at all (deactivated, deleted) — drop their remembered
    # status so a reactivated material starts from a clean slate rather than an alert
    # silently never firing again because it "already" transitioned once, years ago.
    for stale_key in set(previous) - seen_keys:
        await _clear_alert_state(session, _MATERIAL_FORECAST_KEY, stale_key)


async def check_order_unfulfillable_alerts(session: AsyncSession) -> None:
    """Fires once per order line the moment it becomes unfulfillable, then stays quiet for
    that line until it's resolved (allocated or cancelled) and, if it happens again, goes
    short a second time."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.order_unfulfillable)
    if config is None or not config.enabled:
        return

    awaiting = await get_orders_awaiting_inventory(session)
    previous_keys = set((await _load_alert_state_map(session, _ORDER_UNFULFILLABLE_KEY)).keys())
    current_keys: set[str] = set()

    for line in awaiting:
        key = str(line.line_id)
        current_keys.add(key)
        if key in previous_keys:
            continue
        product_label = line.variant_name and f"{line.product_name} ({line.variant_name})" or line.product_name
        await dispatch_notification(
            session,
            category=NotificationCategory.order_unfulfillable,
            urgency=NotificationUrgency.immediate,
            title="Order can't be fully allocated",
            body=f"Order #{line.order_id} — {product_label or 'a line'} is short by {line.short_by}.",
            delivery_mode=config.delivery_mode,
            related_entity_type="order",
            related_entity_id=line.order_id,
        )
        await _set_alert_state(session, _ORDER_UNFULFILLABLE_KEY, key, "alerted")

    for resolved_key in previous_keys - current_keys:
        await _clear_alert_state(session, _ORDER_UNFULFILLABLE_KEY, resolved_key)


async def check_pending_order_threshold(session: AsyncSession) -> None:
    """Fires when the open (pending) order count crosses above the configured threshold, and
    stays quiet until it drops back under and crosses again — a standing backlog of 30 open
    orders shouldn't re-alert every 15 minutes forever."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.pending_order_threshold)
    if config is None or not config.enabled:
        return

    settings = await get_notification_settings(session)
    threshold = settings.pending_order_threshold

    count = (
        await session.execute(select(func.count()).select_from(Order).where(Order.status == OrderStatus.pending))
    ).scalar_one()

    entity_key = "global"
    prior_row = await _get_alert_state(session, _PENDING_ORDER_THRESHOLD_KEY, entity_key)
    was_over = prior_row is not None and prior_row.last_status == "over"
    is_over = count >= threshold

    if is_over and not was_over:
        await dispatch_notification(
            session,
            category=NotificationCategory.pending_order_threshold,
            urgency=NotificationUrgency.digest,
            title="Pending order backlog is growing",
            body=f"{count} orders are pending — above the configured threshold of {threshold}.",
            delivery_mode=config.delivery_mode,
        )
    await _set_alert_state(session, _PENDING_ORDER_THRESHOLD_KEY, entity_key, "over" if is_over else "under")


async def check_platform_api_usage_alerts(session: AsyncSession, platform: ListingPlatform) -> None:
    """Fires once when a platform's daily API usage crosses the soft (80%) or hard (95%)
    limit computed by platform_api_usage — those limits are already used to make
    listing_push/reconcile stand down; this is the first thing that turns them into a
    notification too."""
    type_settings = await get_type_settings_map(session)
    soft_config = type_settings.get(NotificationCategory.marketplace_api_soft_limit)
    hard_config = type_settings.get(NotificationCategory.marketplace_api_hard_limit)
    if (soft_config is None or not soft_config.enabled) and (hard_config is None or not hard_config.enabled):
        return

    usage = await platform_api_usage.usage_today(session, platform)
    budget = platform_api_usage.daily_budget(platform)
    entity_key = platform.value

    if hard_config is not None and hard_config.enabled:
        is_over = usage >= platform_api_usage.hard_limit(platform)
        prior_row = await _get_alert_state(session, _API_HARD_LIMIT_KEY, entity_key)
        was_over = prior_row is not None and prior_row.last_status == "over"
        if is_over and not was_over:
            await dispatch_notification(
                session,
                category=NotificationCategory.marketplace_api_hard_limit,
                urgency=NotificationUrgency.immediate,
                title=f"{platform.value.capitalize()} API usage at hard limit",
                body=f"{usage} of {budget} daily calls used — automatic syncing may be throttled.",
                delivery_mode=hard_config.delivery_mode,
                related_entity_type="platform",
            )
        await _set_alert_state(session, _API_HARD_LIMIT_KEY, entity_key, "over" if is_over else "under")

    if soft_config is not None and soft_config.enabled:
        is_over = usage >= platform_api_usage.soft_limit(platform)
        prior_row = await _get_alert_state(session, _API_SOFT_LIMIT_KEY, entity_key)
        was_over = prior_row is not None and prior_row.last_status == "over"
        if is_over and not was_over:
            await dispatch_notification(
                session,
                category=NotificationCategory.marketplace_api_soft_limit,
                urgency=NotificationUrgency.digest,
                title=f"{platform.value.capitalize()} API usage approaching limit",
                body=f"{usage} of {budget} daily calls used.",
                delivery_mode=soft_config.delivery_mode,
                related_entity_type="platform",
            )
        await _set_alert_state(session, _API_SOFT_LIMIT_KEY, entity_key, "over" if is_over else "under")
