from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class LowStockMaterial(BaseModel):
    id: int
    name: str
    current_qty: Decimal
    reorder_threshold: Decimal
    on_order_qty: Decimal = Decimal(0)


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
