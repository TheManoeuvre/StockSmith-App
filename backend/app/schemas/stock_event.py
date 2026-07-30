from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.product_stock_event import ProductStockEventType
from app.models.stock_adjustment import StockAdjustmentMode


class ProductStockEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    event_type: ProductStockEventType
    qty_delta: int
    running_balance: int
    reason: str | None
    created_at: datetime

    # Populated only for the matching event_type — the detail its source row carries
    # that this ledger row itself doesn't duplicate.
    source_build_id: int | None = None
    build_qty_built: int | None = None
    build_qty_failed: int | None = None

    source_adjustment_id: int | None = None
    adjustment_mode: StockAdjustmentMode | None = None
    adjustment_target_qty: int | None = None

    source_order_line_id: int | None = None
    order_id: int | None = None
    order_external_order_id: str | None = None
