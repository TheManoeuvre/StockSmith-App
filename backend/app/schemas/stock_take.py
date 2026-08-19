from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.material import MaterialCategory
from app.models.stock_take import StockTakeLineStatus, StockTakeStatus


class StockTakeScope(BaseModel):
    """What to count. Shared by preview-scope and create, so the preview is guaranteed to
    describe the same candidate set the take would actually contain."""

    include_materials: bool = False
    include_products: bool = False
    material_categories: list[MaterialCategory] = []
    product_category_ids: list[int] = []
    # Narrows to items whose cadence says they're due (services/abc.py). Combines with the
    # category/type filters rather than replacing them.
    overdue_only: bool = False


class ScopeWarning(BaseModel):
    """A soft lock — the item is already being counted on another open take.

    Warned about, never blocked: two takes covering one item is unusual but not wrong, and
    refusing would leave someone stuck behind a take they'd forgotten to close.
    """

    name: str
    other_stock_take_id: int
    other_started_at: datetime


class ScopePreview(BaseModel):
    candidate_count: int
    material_count: int
    product_count: int
    scope_description: str
    warnings: list[ScopeWarning]


class StockTakeLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    unit: str
    # Where this line sits in the sheet: Products or Materials, then the category, then the
    # parent SKU or material type. Resolved server-side so the count sheet, the CSV and the
    # variances list cannot drift into three different arrangements.
    section: str
    group: str
    subgroup: str
    expected_qty: Decimal
    # Finished-goods lines only: how much of expected_qty is picked for open orders and so
    # probably boxed rather than on the shelf.
    allocated_qty_at_start: Decimal | None
    counted_qty: Decimal | None
    notes: str | None
    status: StockTakeLineStatus
    system_qty_at_approval: Decimal | None
    conflict_reason: str | None
    # counted_qty - expected_qty, or None when nothing was counted. Computed rather than
    # stored so it can't drift from the two numbers it's derived from.
    delta: Decimal | None


class StockTakeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: StockTakeStatus
    includes_materials: bool
    includes_products: bool
    overdue_only: bool
    scope_description: str
    started_at: datetime
    closed_at: datetime | None
    notes: str | None
    # Days since started_at. Visibility only — a take is never auto-expired or auto-closed;
    # the longer one stays open the more lines land in manual review, and seeing that is
    # the point.
    open_days: int
    line_count: int
    counted_count: int
    pending_count: int
    conflict_count: int


class StockTakeDetail(StockTakeRead):
    lines: list[StockTakeLineRead]


class StockTakeCreated(BaseModel):
    stock_take: StockTakeDetail
    warnings: list[ScopeWarning]


class LineCountUpdate(BaseModel):
    """None clears the count, which is not the same as zero — see StockTakeLine."""

    counted_qty: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class BulkLineCount(LineCountUpdate):
    line_id: int


class BulkLineCountRequest(BaseModel):
    lines: list[BulkLineCount]


class LineResolution(BaseModel):
    action: str  # accept_counted | accept_system | reset


class ApproveResult(BaseModel):
    stock_take: StockTakeDetail
    applied_count: int
    conflict_count: int
    skipped_count: int


class UnresolvedVariance(BaseModel):
    """A flagged line on a take that has since closed — a standing variance.

    Carries its take's id and date because the whole point of this view is to be readable
    without remembering which take a line came from.
    """

    line: StockTakeLineRead
    stock_take_id: int
    stock_take_closed_at: datetime | None


class StockTakeImportRow(BaseModel):
    row: int
    error: str


class StockTakeImportResult(BaseModel):
    matched: int
    skipped_blank: int
    failed: list[StockTakeImportRow]
    # False for a dry run, and false when on_error="fail" refused the whole file. The
    # client uses this to decide whether it still needs to confirm.
    applied: bool
