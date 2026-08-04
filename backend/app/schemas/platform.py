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
    # Non-null while at least one unpaid order is holding the sync window open. Surfaced
    # so the hold isn't invisible state that silently widens every fetch — and so a hold
    # that gets stuck on an order that will never settle has a visible symptom.
    unpaid_hold_since: datetime | None
    # True when the connection's granted scopes are missing something StockSmith now
    # needs (currently: eBay's base api_scope, required for the Trading API calls the
    # unmigrated-listing adoption feature uses). Such a connection still syncs orders and
    # pushes quantities perfectly well, so nothing else in the app would reveal the gap —
    # without this it only surfaces as a confusing failure the first time someone opens
    # the listing picker.
    needs_reconnect: bool = False
    needs_reconnect_reason: str | None = None


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
    # StockSmith's reading of whether the money has landed ("settled" / "unsettled" /
    # "reversed"), and whether a real sync would import this order. Preview shows unpaid
    # orders rather than hiding them, so these two can be checked against the untouched
    # `raw` payload's own is_paid / orderPaymentStatus field below.
    payment_state: str
    would_import: bool
    lines: list[SyncPreviewLine]
    raw: dict


class SyncPreviewResult(BaseModel):
    fetched_count: int
    new_count: int
    needs_mapping_count: int
    skipped_unpaid_count: int
    orders: list[SyncPreviewOrder]


class SyncCommitResult(BaseModel):
    fetched_count: int
    created_count: int
    updated_count: int
    needs_mapping_count: int
    shipped_count: int
    skipped_unpaid_count: int
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
    skipped_unpaid_count: int
    error_message: str | None


class SyncRunPage(BaseModel):
    items: list[SyncRunRead]
    total: int


class PlatformSyncSummary(BaseModel):
    """One platform's sync health for the menu-bar indicator — see
    services/sync_status.py for why this is separate from PlatformStatus."""

    platform: ListingPlatform
    connected: bool
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    # Listings whose most recent outbound quantity push failed and was never retried.
    # Distinct from last_sync_error, which only covers inbound order sync — a shop can be
    # importing orders perfectly while silently failing to push stock back, which is the
    # overselling risk this surfaces.
    failing_push_count: int


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
