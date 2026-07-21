from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.listing import ListingPlatform


class PlatformConnectResponse(BaseModel):
    authorize_url: str


class PlatformStatus(BaseModel):
    connected: bool
    account_id: str | None
    shop_name: str | None
    has_shop_icon: bool
    scopes: str | None
    connected_at: datetime | None
    sync_start_date: date | None
    last_orders_synced_at: datetime | None
    last_refreshed_at: datetime | None


class SyncStartDateUpdate(BaseModel):
    sync_start_date: date


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
