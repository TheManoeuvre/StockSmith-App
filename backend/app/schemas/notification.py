from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification import NotificationCategory, NotificationDeliveryMode, NotificationUrgency, SummaryFrequency


class NotificationTypeSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_type: NotificationCategory
    enabled: bool
    delivery_mode: NotificationDeliveryMode


class NotificationTypeSettingUpdate(BaseModel):
    alert_type: NotificationCategory
    enabled: bool
    delivery_mode: NotificationDeliveryMode


class NotificationSettingsRead(BaseModel):
    windows_notifications_enabled: bool
    pushover_enabled: bool
    # Last 4 characters only — see services/notifications.mask_pushover_key. None when no
    # key is stored.
    pushover_user_key_masked: str | None

    quiet_hours_enabled: bool
    quiet_hours_start: int
    quiet_hours_end: int

    daily_summary_enabled: bool
    daily_summary_frequency: SummaryFrequency
    daily_summary_hour_local: int
    daily_summary_day_of_week: int | None

    pending_order_threshold: int

    alert_types: list[NotificationTypeSettingRead]


class NotificationSettingsUpdate(BaseModel):
    windows_notifications_enabled: bool
    pushover_enabled: bool
    # This is a full-settings PUT, not a partial patch, so the semantics are: omit/leave as
    # the masked placeholder returned by GET to keep the stored key unchanged, send an empty
    # string to clear it, or send a new value to replace it. See routers/notifications.py's
    # _resolve_pushover_key for the masked-echo detection.
    pushover_user_key: str | None = None

    quiet_hours_enabled: bool
    quiet_hours_start: int = Field(ge=0, le=23)
    quiet_hours_end: int = Field(ge=0, le=23)

    daily_summary_enabled: bool
    daily_summary_frequency: SummaryFrequency
    daily_summary_hour_local: int = Field(ge=0, le=23)
    daily_summary_day_of_week: int | None = Field(default=None, ge=0, le=6)

    pending_order_threshold: int = Field(ge=1)

    alert_types: list[NotificationTypeSettingUpdate]


class PushoverTestResult(BaseModel):
    success: bool
    reason: str | None = None


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: NotificationCategory
    urgency: NotificationUrgency
    title: str
    body: str
    related_entity_type: str | None
    related_entity_id: int | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationRead]
    total: int


class UnreadCount(BaseModel):
    count: int
