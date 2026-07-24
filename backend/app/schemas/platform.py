from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.listing import ListingPlatform
from app.models.platform_credential import PlatformEnvironment


class PlatformConnectResponse(BaseModel):
    authorize_url: str


class PlatformStatus(BaseModel):
    connected: bool
    account_id: str | None
    shop_name: str | None
    has_shop_icon: bool
    scopes: str | None
    # Only meaningful for eBay — Etsy connections are always 'production'.
    environment: PlatformEnvironment
    connected_at: datetime | None
    sync_start_date: date | None
    last_orders_synced_at: datetime | None
    last_refreshed_at: datetime | None
    auto_sync_enabled: bool
    sync_interval_minutes: int
    # Derived from the most recent commit-mode PlatformSyncRun — distinct from
    # last_orders_synced_at, which is a sync *watermark* that only ever advances on
    # success. These answer "is the most recent attempt (manual or background) actually
    # working," which the watermark alone can't: a failing auto-sync cycle never moves
    # the watermark, so without these a stuck connection would look identical to a
    # healthy but quiet one.
    last_sync_attempt_at: datetime | None
    last_sync_success_at: datetime | None
    last_sync_error: str | None


class SyncStartDateUpdate(BaseModel):
    sync_start_date: date


class SyncSettingsUpdate(BaseModel):
    auto_sync_enabled: bool | None = None
    sync_interval_minutes: int | None = None


class PlatformCredentialRead(BaseModel):
    platform: ListingPlatform
    environment: PlatformEnvironment
    client_id: str | None
    # Never the secret itself — only whether one is stored. See
    # services/platform_credentials.get_status.
    client_secret_set: bool
    public_base_url: str | None
    ru_name: str | None


class PlatformCredentialWrite(BaseModel):
    """All fields optional and, when omitted, left unchanged (see
    services/platform_credentials.upsert_credentials) — the UI never re-displays a
    stored secret, so a save that isn't changing it shouldn't have to resubmit it."""

    client_id: str | None = None
    client_secret: str | None = None
    public_base_url: str | None = None
    ru_name: str | None = None


class SyncPreviewLine(BaseModel):
    external_line_id: str
    sku: str | None
    qty: int
    matched_product_id: int | None
    matched_product_name: str | None
    matched_variant_id: int | None
    matched_variant_name: str | None


class SyncPreviewOrder(BaseModel):
    external_order_id: str
    buyer_name: str | None
    placed_at: datetime
    is_cancelled: bool
    is_shipped: bool
    already_imported: bool
    lines: list[SyncPreviewLine]
    raw: dict


class SyncPreviewResult(BaseModel):
    fetched_count: int
    new_count: int
    needs_mapping_count: int
    orders: list[SyncPreviewOrder]


class SyncCommitResult(BaseModel):
    fetched_count: int
    created_count: int
    updated_count: int
    needs_mapping_count: int
    shipped_count: int
    order_ids: list[int]


class SyncRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: ListingPlatform
    mode: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    fetched_count: int
    new_count: int
    needs_mapping_count: int
    shipped_count: int
    error_message: str | None


class SyncRunPage(BaseModel):
    items: list[SyncRunRead]
    total: int


class ListingPushRead(BaseModel):
    id: int
    product_id: int | None
    product_name: str | None
    variant_id: int | None
    variant_name: str | None
    platform: ListingPlatform
    attempted_qty: int
    status: str
    error_message: str | None
    attempted_at: datetime


class ListingPushPage(BaseModel):
    items: list[ListingPushRead]
    total: int
