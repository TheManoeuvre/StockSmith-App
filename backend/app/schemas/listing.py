import enum
from datetime import datetime

from pydantic import BaseModel


class ListingSyncStatus(str, enum.Enum):
    synced = "synced"
    listing_not_active = "listing_not_active"
    not_found = "not_found"
    not_tested = "not_tested"


class ProductSyncStatus(str, enum.Enum):
    synced = "synced"
    partial = "partial"
    not_found = "not_found"
    not_tested = "not_tested"


class UnitSyncResult(BaseModel):
    variant_id: int | None
    variant_name: str | None
    sku: str | None
    status: ListingSyncStatus
    external_listing_id: str | None
    external_title: str | None
    external_variation: str | None
    external_state: str | None
    external_quantity: int | None
    last_checked_at: datetime | None


class ProductListingSyncSummary(BaseModel):
    product_id: int
    product_status: ProductSyncStatus
    units: list[UnitSyncResult]


class BulkListingSyncResult(BaseModel):
    summaries: list[ProductListingSyncSummary]
    synced_count: int
    partial_count: int
    not_found_count: int
