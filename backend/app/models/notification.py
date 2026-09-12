import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.services.crypto import EncryptedString


class NotificationCategory(str, enum.Enum):
    """One value per alert type the app can raise, plus `daily_summary` for the periodic
    order-summary notification (which isn't user-configurable per-type the way the other
    ten are — see NotificationSettings.daily_summary_* instead)."""

    marketplace_sync_failure = "marketplace_sync_failure"
    material_forecast_critical = "material_forecast_critical"
    material_forecast_warning = "material_forecast_warning"
    # Fires for an order line that's short on finished-goods stock but has a BOM (or is a
    # bundle) it could still be built/assembled from — the common, expected case for a
    # maker. Kept lower-urgency than order_blocked below; see check_order_unfulfillable_alerts.
    order_unfulfillable = "order_unfulfillable"
    # Fires for an order line that's short on stock AND has no BOM/kitting BOM at all to
    # ever close that shortfall by building more — a real blocker, not just a wait for
    # materials. Distinct from order_unfulfillable so the two can carry different default
    # urgency/delivery without one drowning out the other.
    order_blocked = "order_blocked"
    pending_order_threshold = "pending_order_threshold"
    backup_failed = "backup_failed"
    secondary_backup_unreachable = "secondary_backup_unreachable"
    marketplace_api_soft_limit = "marketplace_api_soft_limit"
    marketplace_api_hard_limit = "marketplace_api_hard_limit"
    daily_summary = "daily_summary"


# The 10 user-configurable alert types — every NotificationCategory except daily_summary,
# which always fires (see services/notification_summary.py).
ALERT_TYPES: tuple[NotificationCategory, ...] = (
    NotificationCategory.marketplace_sync_failure,
    NotificationCategory.material_forecast_critical,
    NotificationCategory.material_forecast_warning,
    NotificationCategory.order_unfulfillable,
    NotificationCategory.order_blocked,
    NotificationCategory.pending_order_threshold,
    NotificationCategory.backup_failed,
    NotificationCategory.secondary_backup_unreachable,
    NotificationCategory.marketplace_api_soft_limit,
    NotificationCategory.marketplace_api_hard_limit,
)

# Seed defaults: the alert types that most directly need a human's attention right away
# default to immediate; the rest (which are naturally either high-volume or lower-urgency)
# default to batching into the digest. order_unfulfillable (short on stock, but buildable)
# is expected/routine for a maker and defaults to digest; order_blocked (no BOM to build
# from at all) is the real emergency and defaults to immediate.
DEFAULT_IMMEDIATE_ALERT_TYPES: frozenset[NotificationCategory] = frozenset(
    {
        NotificationCategory.marketplace_sync_failure,
        NotificationCategory.material_forecast_critical,
        NotificationCategory.order_blocked,
        NotificationCategory.backup_failed,
        NotificationCategory.marketplace_api_hard_limit,
    }
)


class NotificationUrgency(str, enum.Enum):
    immediate = "immediate"
    digest = "digest"


class NotificationDeliveryMode(str, enum.Enum):
    immediate = "immediate"
    digest = "digest"
    off = "off"


class SummaryFrequency(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"


class NotificationSettings(Base):
    """Single-row (id=1) notification configuration — channels, quiet hours, and the
    daily/weekly order-summary schedule. Per-alert-type enable/delivery-mode lives on
    NotificationTypeSettings instead (one row per type), the same split GeneralSettings
    doesn't need but BackupSettings' secondary_dir/last_run_* pairing already models: shop-
    wide config here, per-item state broken out.

    pushover_user_key is encrypted at rest with the same Fernet key/column type that
    protects marketplace OAuth tokens (see PlatformAppCredential/services/crypto.py) — a
    Pushover user key isn't as sensitive as an OAuth grant, but there's no reason to invent
    a second storage convention for one more secret column.
    """

    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint("quiet_hours_start >= 0 AND quiet_hours_start <= 23", name="ck_notif_quiet_start_range"),
        CheckConstraint("quiet_hours_end >= 0 AND quiet_hours_end <= 23", name="ck_notif_quiet_end_range"),
        CheckConstraint(
            "daily_summary_hour_local >= 0 AND daily_summary_hour_local <= 23", name="ck_notif_summary_hour_range"
        ),
        CheckConstraint(
            "daily_summary_day_of_week IS NULL OR (daily_summary_day_of_week >= 0 AND daily_summary_day_of_week <= 6)",
            name="ck_notif_summary_dow_range",
        ),
        CheckConstraint("pending_order_threshold >= 1", name="ck_notif_pending_order_threshold_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    windows_notifications_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    pushover_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    pushover_user_key: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)

    quiet_hours_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    quiet_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    quiet_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=7)

    daily_summary_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    daily_summary_frequency: Mapped[SummaryFrequency] = mapped_column(
        portable_enum(SummaryFrequency, name="notification_summary_frequency"),
        nullable=False,
        default=SummaryFrequency.daily,
    )
    daily_summary_hour_local: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    # Only meaningful when daily_summary_frequency == weekly (Monday=0 .. Sunday=6, matching
    # datetime.weekday()); ignored for daily. NULL is a valid state even while weekly is
    # selected in a half-filled-out form — the scheduler simply never fires until it's set.
    daily_summary_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_summary_last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Threshold for the "pending order count over threshold" alert — kept configurable
    # rather than hard-coded so a shop's normal backlog size doesn't spam this forever.
    pending_order_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationTypeSettings(Base):
    """One row per alert type (see ALERT_TYPES) — whether it's on at all, and how it
    delivers: immediate (dispatched right away, subject to quiet hours), digest (batched
    into the next digest flush), or off (skipped entirely — not even logged in-app; see
    services/notifications.dispatch_notification)."""

    __tablename__ = "notification_type_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_type: Mapped[NotificationCategory] = mapped_column(
        portable_enum(NotificationCategory, name="notification_category"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    delivery_mode: Mapped[NotificationDeliveryMode] = mapped_column(
        portable_enum(NotificationDeliveryMode, name="notification_delivery_mode"),
        nullable=False,
        default=NotificationDeliveryMode.immediate,
    )


class Notification(Base):
    """The in-app notification log — source of truth for every delivery StockSmith has ever
    raised, whether or not it also went out over Pushover or a Windows toast.

    digest_sent_at is separate from read_at: read_at means a person opened it in the app;
    digest_sent_at means the periodic digest flush (services/notifications.flush_digest)
    already folded this row into a batched external send, so it must not be sent again on
    the next flush. A digest item can be flushed long before anyone reads it, or read in-app
    before it's ever flushed (and should still not be re-sent).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[NotificationCategory] = mapped_column(
        portable_enum(NotificationCategory, name="notification_category"), nullable=False
    )
    urgency: Mapped[NotificationUrgency] = mapped_column(
        portable_enum(NotificationUrgency, name="notification_urgency"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    related_entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    related_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    digest_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationAlertState(Base):
    """Per-entity dedup memory for alert conditions that must fire only on a *transition*
    rather than on every periodic recompute — a material's forecast status moving into
    'critical', an order line newly becoming unfulfillable, a usage counter newly crossing a
    limit. Without this, re-polling the same still-critical material or still-short order
    line every 15 minutes would re-alert every 15 minutes forever.

    alert_key is a short internal namespace string (e.g. "material_forecast_status",
    "order_unfulfillable", "order_blocked") rather than the NotificationCategory enum — some alert
    conditions (the material forecast) share one status field across two categories
    (critical/warning), so the dedup key needs to be coarser than the category that ends up
    dispatched. entity_key is the stringified id of whatever the alert is about (material
    id, order line id, platform name) — plain string because the different alert kinds
    sharing this table point at entirely different tables, and nothing here needs to join
    back to them.
    """

    __tablename__ = "notification_alert_state"
    __table_args__ = (UniqueConstraint("alert_key", "entity_key", name="uq_notification_alert_state_key_entity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_key: Mapped[str] = mapped_column(String, nullable=False)
    entity_key: Mapped[str] = mapped_column(String, nullable=False)
    last_status: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
