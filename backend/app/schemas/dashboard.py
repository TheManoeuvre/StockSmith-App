from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


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


class DashboardSummary(BaseModel):
    total_inventory_value: Decimal
    active_product_count: int
    low_stock_materials: list[LowStockMaterial]
    lowest_buildable_products: list[BuildableProduct]
    margin_alerts: list[MarginAlert]
    orders_awaiting_inventory: list[OrderAwaitingInventory]
    orders_awaiting_packaging: list[OrderAwaitingPackaging] = []
