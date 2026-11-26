from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.stock_adjustment import StockAdjustmentMode


class StockAdjustmentCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    mode: StockAdjustmentMode = StockAdjustmentMode.adjust
    value: int
    reason: str
    # Where a decrease comes from when the item is kept in several places. Optional.
    from_location_id: int | None = None


class StockAdjustmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    mode: StockAdjustmentMode
    qty_delta: int
    target_qty: int | None
    reason: str
    created_at: datetime
