from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.purchase import PurchaseStatus


class PurchaseLineInput(BaseModel):
    material_id: int
    qty: Decimal
    total_cost: Decimal
    notes: str | None = None


class PurchaseCreate(BaseModel):
    supplier_id: int | None = None
    order_date: date | None = None
    notes: str | None = None
    lines: list[PurchaseLineInput]


class PurchaseUpdate(BaseModel):
    supplier_id: int | None = None
    order_date: date | None = None
    notes: str | None = None


class PurchaseLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_id: int
    material_id: int
    qty: Decimal
    total_cost: Decimal
    notes: str | None = None


class MaterialStockHistoryRead(BaseModel):
    """A single row in a material's unified stock timeline — either a purchase line
    (kind='purchase') or a manual adjustment (kind='adjustment'), merged and ordered
    chronologically. Used by GET /materials/{id}/stock-history."""

    id: int
    kind: Literal["purchase", "adjustment"]
    at: datetime
    qty: Decimal
    total_cost: Decimal | None
    status: PurchaseStatus | None
    supplier_name: str | None
    reason: str | None
    mode: str | None
    target_qty: Decimal | None
    product_id: int | None
    product_name: str | None
    variant_id: int | None
    order_id: int | None


class PurchaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int | None
    supplier_name: str | None = None
    order_date: date
    status: PurchaseStatus
    received_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    lines: list[PurchaseLineRead] = []
