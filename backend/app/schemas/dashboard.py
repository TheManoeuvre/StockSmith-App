from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.abc import DueForCountItemRead


class LowStockMaterial(BaseModel):
    id: int
    name: str
    current_qty: Decimal
    reorder_threshold: Decimal
    on_order_qty: Decimal = Decimal(0)
    allocated_qty: Decimal = Decimal(0)
    supplier_id: int | None = None
    supplier_name: str | None = None
    # None on both when there isn't enough sales history to forecast yet — status is then
    # "insufficient_data" and the material is only listed because it's at/below its
    # (static) reorder_threshold, same as pre-forecast behavior.
    consumption_rate_per_week: Decimal | None = None
    weeks_of_supply: Decimal | None = None
    # How much of weeks_of_supply comes from finished-goods stock delaying the material
    # draw, rather than the material itself — see forecasting.py. Always 0 when
    # weeks_of_supply is None.
    fg_buffer_weeks: Decimal | None = None
    status: str = "insufficient_data"  # "critical" | "warning" | "insufficient_data"


class BuildableProduct(BaseModel):
    product_id: int
    name: str
    max_buildable: int | None
    expected_max_buildable: int | None = None


class MarginAlert(BaseModel):
    product_id: int
    name: str
    previous_margin_percent: Decimal
    current_margin_percent: Decimal


class OrderAwaitingInventory(BaseModel):
    line_id: int
    order_id: int
    product_id: int | None
    variant_id: int | None
    product_name: str | None
    variant_name: str | None
    short_by: int
    order_placed_at: datetime


class OrderAwaitingPackaging(BaseModel):
    order_id: int
    material_id: int
    material_name: str
    short_by: Decimal
    order_placed_at: datetime


class OpenStockTake(BaseModel):
    id: int
    started_at: datetime
    open_days: int
    line_count: int
    counted_count: int


class DashboardSummary(BaseModel):
    total_inventory_value: Decimal
    active_product_count: int
    low_stock_materials: list[LowStockMaterial]
    lowest_buildable_products: list[BuildableProduct]
    margin_alerts: list[MarginAlert]
    orders_awaiting_inventory: list[OrderAwaitingInventory]
    orders_awaiting_packaging: list[OrderAwaitingPackaging] = []
    # Capped at 10; items_due_for_count_total is the uncapped count, so the dashboard can
    # say how many it isn't showing. Both default so an older client parsing this payload
    # doesn't break on the new fields.
    items_due_for_count: list[DueForCountItemRead] = []
    items_due_for_count_total: int = 0
    # Flagged lines on takes that have since closed. A count rather than the rows: the
    # dashboard's job is to say follow-up is outstanding and send you to the view that
    # lists it, not to reproduce that view.
    unresolved_variance_count: int = 0
    # The take currently in progress, if any, with how long it has been open. Visibility
    # only — nothing expires a take; the longer one runs the more lines land in manual
    # review, and noticing that is the whole point.
    open_stock_take: OpenStockTake | None = None
