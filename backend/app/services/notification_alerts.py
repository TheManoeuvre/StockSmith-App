"""Alert detection for the 10 configurable notification types.

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
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.notification import NotificationAlertState, NotificationCategory, NotificationDeliveryMode, NotificationUrgency
from app.models.order import Order, OrderStatus
from app.services import platform_api_usage
from app.services.buildability import get_orders_awaiting_inventory
from app.services.forecasting import compute_material_forecasts
from app.services.notifications import (
    dispatch_notification,
    get_notification_settings,
    get_type_settings_map,
    resolve_alerts,
)

logger = logging.getLogger("stocksmith.notification_alerts")

_MATERIAL_FORECAST_KEY = "material_forecast_status"
_ORDER_UNFULFILLABLE_KEY = "order_unfulfillable"
_ORDER_BLOCKED_KEY = "order_blocked"
_PENDING_ORDER_THRESHOLD_KEY = "pending_order_threshold"
_API_SOFT_LIMIT_KEY = "marketplace_api_soft_limit"
_API_HARD_LIMIT_KEY = "marketplace_api_hard_limit"
_SHIPPING_PROFILE_MISSING_KEY = "shipping_profile_missing"

# Items listed in a grouped order-shortfall body before it collapses into "...and N more".
_ORDER_BODY_LINE_CAP = 6


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


async def raise_platform_reconnect_required_alert(session: AsyncSession, platform: ListingPlatform) -> None:
    """Called from sync_scheduler._record_auth_failure the moment auto-sync disables itself
    after _MAX_CONSECUTIVE_AUTH_FAILURES. Needs no separate dedup state (contrast the
    check_* functions above): that disable only happens once per failure episode — the
    background loop stops ticking that platform the instant auto_sync_enabled goes False, so
    nothing can call this again until the connection is reconnected and auto-sync is turned
    back on."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.platform_reconnect_required)
    if config is None or not config.enabled:
        return
    label = platform.value.capitalize()
    await dispatch_notification(
        session,
        category=NotificationCategory.platform_reconnect_required,
        urgency=NotificationUrgency.immediate,
        title=f"{label} needs reconnecting",
        body=(
            f"Auto-sync for {label} has been disabled after repeated authentication "
            "failures. Reconnect the account to resume syncing."
        ),
        delivery_mode=config.delivery_mode,
        related_entity_type="platform",
    )


async def raise_shipping_price_changed_alert(
    session: AsyncSession, platform: ListingPlatform, changes: list[tuple[str, object, object]]
) -> None:
    """Called once per shipping_price_sync.refresh that rewrote at least one price, with
    (profile name, old, new) per profile that moved. One alert listing them all, not one
    per profile: a marketplace-wide postage change touches every profile at once and the
    user wants the list, not a dozen notifications."""
    if not changes:
        return
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.shipping_price_changed)
    if config is None or not config.enabled:
        return
    label = "Etsy" if platform == ListingPlatform.etsy else platform.value.capitalize()

    def _fmt(value: object) -> str:
        return "unset" if value is None else f"£{float(value):.2f}"

    lines = [f"{name}: {_fmt(old)} → {_fmt(new)}" for name, old, new in changes]
    count = len(changes)
    await dispatch_notification(
        session,
        category=NotificationCategory.shipping_price_changed,
        urgency=NotificationUrgency.digest,
        title=f"{label} postage price{'s' if count != 1 else ''} changed for {count} shipping profile{'s' if count != 1 else ''}",
        body=(
            f"Buyer postage prices were refreshed from {label} and product margins now use them:\n"
            + "\n".join(lines)
        ),
        delivery_mode=config.delivery_mode,
        related_entity_type="shipping_profile",
    )


@dataclass
class PendingReviewAlert:
    """What raise_replacement_parcel_review_alert needs, captured by
    order_parcels.apply_postage_charges while its session is still open so the alert can
    be sent after the sync's own commit (dispatch_notification commits, and the sync's
    write phase is one transaction)."""

    order_id: int
    external_order_id: str | None
    platform: ListingPlatform | None
    amount: Decimal | None  # None for a bulk label — see OrderPostageCharge.amount
    currency: str | None
    posted_at: datetime | None


async def raise_replacement_parcel_review_alert(session: AsyncSession, alert: PendingReviewAlert) -> None:
    """Raised once per second-or-later marketplace label that made a sync auto-create a
    replacement parcel. No dedup state: a label has a unique external id and only ever
    spawns a parcel once. Linking a label to a parcel the user already recorded by hand
    does NOT come through here — there's nothing left for them to fill in.

    related_entity_type/id point at the order so the notification centre can open it
    straight onto the Fulfilment tab, where the parcel sits waiting for items and a
    reason. Resolved (marked read) by order_parcels when the parcel is completed or
    deleted."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.replacement_parcel_review)
    if config is None or not config.enabled:
        return
    platform_label = {ListingPlatform.etsy: "Etsy", ListingPlatform.ebay: "eBay"}.get(alert.platform, "Marketplace")
    when = f" on {alert.posted_at:%d %b %Y}" if alert.posted_at is not None else ""
    money = (
        f"{alert.amount:.2f}" + (f" {alert.currency}" if alert.currency else "")
        if alert.amount is not None
        else "cost not itemised by the marketplace"
    )
    await dispatch_notification(
        session,
        category=NotificationCategory.replacement_parcel_review,
        urgency=NotificationUrgency.immediate,
        title=f"Replacement parcel sent for {platform_label} order {alert.external_order_id or alert.order_id}",
        body=(
            f"A second shipping label ({money}) was bought against this order{when}. "
            "Add what was sent and why on the order's Fulfilment tab so stock and profit stay right."
        ),
        delivery_mode=config.delivery_mode,
        related_entity_type="order",
        related_entity_id=alert.order_id,
    )


@dataclass
class PendingCancellationAlert:
    """What raise_order_cancellation_pending_alert needs, captured by
    order_sync._reconcile_status while its session is still open so the alert can be sent
    after the sync's own commit — the same reason PendingReviewAlert above exists.

    reason distinguishes the two situations that set Order.pending_marketplace_cancellation
    (an outright marketplace cancellation vs. a payment reversal) so the notification reads
    accurately rather than always saying "cancelled"."""

    order_id: int
    external_order_id: str | None
    platform: ListingPlatform | None
    reason: str


async def raise_order_cancellation_pending_alert(session: AsyncSession, alert: PendingCancellationAlert) -> None:
    """Raised the moment a sync first sets Order.pending_marketplace_cancellation. Nothing
    local changes automatically when a marketplace reports a cancellation — the order's
    stock reservation stays in place until a human picks a scrap/return-to-stock disposition
    (see services/returns.process_cancellation) — so this is the only prompt that a
    reservation is still live despite the marketplace-side cancellation. No dedup state
    needed: the caller only constructs this once per False->True transition of the flag."""
    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.order_cancellation_pending)
    if config is None or not config.enabled:
        return
    platform_label = {ListingPlatform.etsy: "Etsy", ListingPlatform.ebay: "eBay"}.get(alert.platform, "Marketplace")
    await dispatch_notification(
        session,
        category=NotificationCategory.order_cancellation_pending,
        urgency=NotificationUrgency.immediate,
        title=f"{platform_label} order {alert.external_order_id or alert.order_id} {alert.reason} — review needed",
        body=(
            "Nothing has been changed locally, including reserved stock — review the order and "
            "confirm a scrap/return-to-stock decision to release it."
        ),
        delivery_mode=config.delivery_mode,
        related_entity_type="order",
        related_entity_id=alert.order_id,
    )


async def raise_pending_cancellation_alerts(session: AsyncSession, pending: list[PendingCancellationAlert]) -> None:
    for alert in pending:
        await raise_order_cancellation_pending_alert(session, alert)


async def resolve_order_cancellation_pending_alert(session: AsyncSession, order_id: int) -> None:
    """Marks any unread order_cancellation_pending notification for this order read — called
    once a human resolves the disposition (services/returns.process_cancellation) or a later
    sync finds the flag no longer applies (order_sync._reconcile_status's self-heal branch)."""
    await resolve_alerts(
        session,
        category=NotificationCategory.order_cancellation_pending,
        related_entity_type="order",
        related_entity_id=order_id,
    )


async def check_shipping_profile_missing_alerts(
    session: AsyncSession, platform: ListingPlatform, missing: list, present: list
) -> None:
    """Called by every shipping_price_sync.refresh with the linked local profiles whose
    marketplace profile has vanished (`missing`) and those still found (`present`).

    Deduplicated per profile per platform through NotificationAlertState, the same way
    the periodic check_* functions are: a profile deleted on Etsy stays deleted, and the
    refresh runs daily, so without the state row the same alert would fire every day
    until someone re-linked it. Clears when the profile reappears or is re-linked to
    something that exists, so a later disappearance alerts again."""
    entity = lambda profile: f"{platform.value}:{profile.id}"  # noqa: E731
    flagged = await _load_alert_state_map(session, _SHIPPING_PROFILE_MISSING_KEY)
    for profile in present:
        if entity(profile) in flagged:
            await _clear_alert_state(session, _SHIPPING_PROFILE_MISSING_KEY, entity(profile))

    type_settings = await get_type_settings_map(session)
    config = type_settings.get(NotificationCategory.shipping_profile_missing)
    label = "Etsy" if platform == ListingPlatform.etsy else platform.value.capitalize()
    thing = "shipping profile" if platform == ListingPlatform.etsy else "postage policy"
    for profile in missing:
        if entity(profile) in flagged:
            continue
        if config is not None and config.enabled:
            await dispatch_notification(
                session,
                category=NotificationCategory.shipping_profile_missing,
                urgency=NotificationUrgency.immediate,
                title=f"Shipping profile '{profile.name}' is missing on {label}",
                body=(
                    f"The {label} {thing} that '{profile.name}' is linked to no longer exists. Its last price has "
                    f"been kept, but drafts sent with that {thing} will fail and the margin on products using it "
                    f"can't be verified. Re-link it in Settings › Shipping profiles."
                ),
                delivery_mode=config.delivery_mode,
                related_entity_type="shipping_profile",
                related_entity_id=profile.id,
            )
        await _set_alert_state(session, _SHIPPING_PROFILE_MISSING_KEY, entity(profile), "missing")


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
            elif forecast.status not in ("critical", "warning") and prior_status in ("critical", "warning"):
                # Back to healthy — any unread alert raised while this material was low no
                # longer reflects reality, so mark it read rather than leaving it for a
                # human to dismiss by hand.
                await resolve_alerts(
                    session,
                    category=NotificationCategory.material_forecast_critical,
                    related_entity_type="material",
                    related_entity_id=forecast.material_id,
                )
                await resolve_alerts(
                    session,
                    category=NotificationCategory.material_forecast_warning,
                    related_entity_type="material",
                    related_entity_id=forecast.material_id,
                )
            await _set_alert_state(session, _MATERIAL_FORECAST_KEY, key, forecast.status)

    # Materials no longer forecast at all (deactivated, deleted) — drop their remembered
    # status so a reactivated material starts from a clean slate rather than an alert
    # silently never firing again because it "already" transitioned once, years ago.
    for stale_key in set(previous) - seen_keys:
        await _clear_alert_state(session, _MATERIAL_FORECAST_KEY, stale_key)


def _product_label(line) -> str:
    """"Brick Pencil Pot (6 Stud / Gold)", or just the product name for an unvarianted
    product."""
    if line.variant_name:
        return f"{line.product_name} ({line.variant_name})"
    return line.product_name or "Unnamed product"


def _shortfall_body(new_lines: list, already_flagged: int) -> str:
    """One line per newly-short item, capped, with a tail noting anything already alerted on
    for the same order so the reader isn't left thinking this is the whole picture."""
    body_lines = [f"{_product_label(line)} — short by {line.short_by}" for line in new_lines[:_ORDER_BODY_LINE_CAP]]
    overflow = len(new_lines) - _ORDER_BODY_LINE_CAP
    if overflow > 0:
        body_lines.append(f"...and {overflow} more")
    if already_flagged:
        body_lines.append(
            f"({already_flagged} other item{'s' if already_flagged != 1 else ''} on this order "
            f"{'were' if already_flagged != 1 else 'was'} already flagged.)"
        )
    return "\n".join(body_lines)


async def check_order_unfulfillable_alerts(session: AsyncSession) -> None:
    """Fires once per order line the moment it starts awaiting product (has_bom=True, the
    common/expected case for a maker) or becomes genuinely blocked (has_bom=False — no BOM
    or kitting BOM exists to ever build more), then stays quiet for that line until it's
    resolved (allocated or cancelled) and, if it happens again, goes short a second time.
    The two cases dispatch under separate categories/keys so a routine restock wait never
    shares a dedup slot — or urgency — with a real blocker.

    Dedup stays per *line*, but delivery is grouped per *order*: every line that newly went
    short in this sweep becomes one notification for its order, listing the items. A single
    import of one order with five short variants is one event to a person — they open the
    order, or they go and make the product — so five near-identical pushes, each titled with
    the same category name, was five times the interruption for no extra information. A line
    that goes short later still raises its own (grouped) notification, because that genuinely
    is new information.
    """
    type_settings = await get_type_settings_map(session)
    awaiting_config = type_settings.get(NotificationCategory.order_unfulfillable)
    blocked_config = type_settings.get(NotificationCategory.order_blocked)
    awaiting_on = awaiting_config is not None and awaiting_config.enabled
    blocked_on = blocked_config is not None and blocked_config.enabled
    if not awaiting_on and not blocked_on:
        return

    awaiting = await get_orders_awaiting_inventory(session)
    previous_awaiting_keys = set((await _load_alert_state_map(session, _ORDER_UNFULFILLABLE_KEY)).keys())
    previous_blocked_keys = set((await _load_alert_state_map(session, _ORDER_BLOCKED_KEY)).keys())
    current_awaiting_keys: set[str] = set()
    current_blocked_keys: set[str] = set()

    # order_id -> lines that newly went short in this sweep, and a count of the ones on the
    # same order we've already alerted on (context for the body, not a reason to re-alert).
    new_awaiting: dict[int, list] = {}
    new_blocked: dict[int, list] = {}
    known_awaiting: dict[int, int] = {}
    known_blocked: dict[int, int] = {}

    for line in awaiting:
        key = str(line.line_id)
        if line.has_bom:
            current_awaiting_keys.add(key)
            if key in previous_awaiting_keys:
                known_awaiting[line.order_id] = known_awaiting.get(line.order_id, 0) + 1
            else:
                new_awaiting.setdefault(line.order_id, []).append(line)
        else:
            current_blocked_keys.add(key)
            if key in previous_blocked_keys:
                known_blocked[line.order_id] = known_blocked.get(line.order_id, 0) + 1
            else:
                new_blocked.setdefault(line.order_id, []).append(line)

    if awaiting_on:
        for order_id, lines in sorted(new_awaiting.items()):
            count = len(lines)
            await dispatch_notification(
                session,
                category=NotificationCategory.order_unfulfillable,
                urgency=NotificationUrgency.digest,
                # The title is the only line read at a glance on a lock screen, so it
                # identifies the order and the size of the problem rather than restating the
                # alert category the reader already subscribed to.
                title=f"Order #{order_id} — {count} item{'s' if count != 1 else ''} short",
                body=_shortfall_body(lines, known_awaiting.get(order_id, 0)),
                delivery_mode=awaiting_config.delivery_mode,
                related_entity_type="order",
                related_entity_id=order_id,
            )
            for line in lines:
                await _set_alert_state(session, _ORDER_UNFULFILLABLE_KEY, str(line.line_id), "alerted")

    if blocked_on:
        for order_id, lines in sorted(new_blocked.items()):
            count = len(lines)
            body = _shortfall_body(lines, known_blocked.get(order_id, 0))
            await dispatch_notification(
                session,
                category=NotificationCategory.order_blocked,
                urgency=NotificationUrgency.immediate,
                title=f"Order #{order_id} blocked — {count} item{'s' if count != 1 else ''} with no BOM",
                body=f"{body}\n\nNothing can be built to cover this — these items need a BOM.",
                delivery_mode=blocked_config.delivery_mode,
                related_entity_type="order",
                related_entity_id=order_id,
            )
            for line in lines:
                await _set_alert_state(session, _ORDER_BLOCKED_KEY, str(line.line_id), "alerted")

    for resolved_key in previous_awaiting_keys - current_awaiting_keys:
        await _clear_alert_state(session, _ORDER_UNFULFILLABLE_KEY, resolved_key)
    for resolved_key in previous_blocked_keys - current_blocked_keys:
        await _clear_alert_state(session, _ORDER_BLOCKED_KEY, resolved_key)


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
    elif not is_over and was_over:
        # Backlog dropped back under threshold — the standing alert no longer reflects
        # reality.
        await resolve_alerts(session, category=NotificationCategory.pending_order_threshold)
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
