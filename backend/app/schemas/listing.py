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
    # The quantity listing_push would send for this unit right now — i.e. what the
    # marketplace *should* be showing. None when it can't be computed (no product/variant)
    # or when the caller didn't ask for it: the bulk shop-wide check skips it because
    # it's a per-unit buildability computation and would turn one click into N of them.
    expected_quantity: int | None = None
    # Only ever True when both quantities are known AND the listing was found. A unit
    # that isn't on the marketplace at all is a "not found" problem, not a mismatch —
    # conflating them would offer to "correct" a listing that doesn't exist.
    quantity_mismatch: bool = False


class ProductListingSyncSummary(BaseModel):
    product_id: int
    product_status: ProductSyncStatus
    units: list[UnitSyncResult]


class PushCorrectionsResult(BaseModel):
    """Outcome of a user-confirmed push of StockSmith's quantities to a marketplace."""

    pushed_count: int
    failed_count: int
    # Per-unit failure text, since a partial success is the interesting case: 3 of 4
    # corrected tells the user something a bare count doesn't.
    errors: list[str]


class BulkListingSyncResult(BaseModel):
    summaries: list[ProductListingSyncSummary]
    synced_count: int
    partial_count: int
    not_found_count: int
