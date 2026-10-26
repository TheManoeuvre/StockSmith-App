import enum
from datetime import datetime
from typing import Literal

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


class AdditionalListingResult(BaseModel):
    """A second (or later) marketplace listing that holds the same SKU as the unit's
    primary listing — A/B tests, a free-shipping variant. Quantity pushes reach every one;
    the unit's sync status and rollup stay about the primary alone."""

    listing_id: int
    external_listing_id: str | None
    role: str | None = None
    status: ListingSyncStatus
    external_title: str | None
    external_variation: str | None
    external_state: str | None
    external_quantity: int | None
    last_checked_at: datetime | None
    quantity_mismatch: bool = False


class ListingUpdate(BaseModel):
    """What a seller can change about one linked listing. Fields left out are untouched, so
    `role: null` clears the label while omitting it keeps it."""

    role: str | None = None
    # Only ever "make this the primary" — there is no way to un-primary a listing except by
    # promoting a sibling, since a unit always has exactly one.
    is_primary: Literal[True] | None = None


class UnitSyncResult(BaseModel):
    # The primary listing's row id and label, for editing them. None before a first check.
    listing_id: int | None = None
    role: str | None = None
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
    # Other listings sharing this unit's SKU. quantity_mismatch above is True if the
    # primary OR any of these has drifted, since one "Push corrections" fixes them all.
    additional_listings: list[AdditionalListingResult] = []


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
