from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.listing import ListingPlatform
from app.models.order import OrderStatus


class OrderLineInput(BaseModel):
    product_id: int | None = None
    variant_id: int | None = None
    ordered_qty: int
    unit_price: Decimal | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def _require_product_or_variant(self) -> "OrderLineInput":
        if self.product_id is None and self.variant_id is None:
            raise ValueError("A line needs either product_id or variant_id")
        return self


class OrderCreate(BaseModel):
    buyer_name: str | None = None
    buyer_note: str | None = None
    notes: str | None = None
    lines: list[OrderLineInput]


class OrderUpdate(BaseModel):
    buyer_name: str | None = None
    buyer_note: str | None = None
    notes: str | None = None


class OrderLineQtyUpdate(BaseModel):
    ordered_qty: int


class DeallocateRequest(BaseModel):
    qty: int


class MapSkuRequest(BaseModel):
    product_id: int | None = None
    variant_id: int | None = None

    @model_validator(mode="after")
    def _require_product_or_variant(self) -> "MapSkuRequest":
        if self.product_id is None and self.variant_id is None:
            raise ValueError("Must map to either product_id or variant_id")
        return self


class CreateProductAndMapRequest(BaseModel):
    name: str
    sku: str | None = None


class OrderLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    product_id: int | None
    variant_id: int | None
    product_name: str | None = None
    variant_name: str | None = None
    sku: str | None
    ordered_qty: int
    allocated_qty: int
    shipped_qty: int
    unit_price: Decimal | None
    currency: str | None
    external_line_id: str | None
    needs_mapping: bool
    cost_per_unit_snapshot: Decimal | None = None
    kitting_cost_per_unit_snapshot: Decimal | None = None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: ListingPlatform | None
    external_order_id: str | None
    status: OrderStatus
    buyer_name: str | None
    buyer_note: str | None
    order_placed_at: datetime
    shipped_at: datetime | None
    cancelled_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    grand_total: Decimal | None = None
    subtotal: Decimal | None = None
    shipping_charged: Decimal | None = None
    tax_charged: Decimal | None = None
    vat_charged: Decimal | None = None
    discount_amount: Decimal | None = None
    refunded_amount: Decimal | None = None
    currency: str | None = None
    payment_fees: Decimal | None = None
    payment_net: Decimal | None = None
    payment_status: str | None = None
    financials_synced_at: datetime | None = None
    net_profit: Decimal | None = None
    sync_issue: str | None = None
    lines: list[OrderLineRead] = []
