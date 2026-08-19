from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.purchase import PurchaseStatus


class PurchaseLineInput(BaseModel):
    # Present for a line that already exists, absent for one being added. PUT /lines is an
    # upsert keyed on this, not the delete-and-reinsert it used to be — line identity has to
    # survive an edit now that receipts hang off it.
    id: int | None = None
    material_id: int
    qty: Decimal
    total_cost: Decimal
    notes: str | None = None


class PurchaseCreate(BaseModel):
    supplier_id: int | None = None
    order_date: date | None = None
    expected_arrival_date: date | None = None
    notes: str | None = None
    lines: list[PurchaseLineInput]


class PurchaseUpdate(BaseModel):
    supplier_id: int | None = None
    order_date: date | None = None
    expected_arrival_date: date | None = None
    notes: str | None = None


class PurchaseReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_line_id: int
    qty: Decimal
    # NULL means this delivery took its pro-rata share of the line total rather than being
    # billed separately — see services/costing.py for how the share is worked out.
    total_cost: Decimal | None
    received_at: datetime
    notes: str | None = None
    batch_id: str | None = None


class PurchaseReceiptLineInput(BaseModel):
    line_id: int
    qty: Decimal
    total_cost: Decimal | None = None


class PurchaseReceiptsCreate(BaseModel):
    """One delivery, covering however many lines of the order turned up in it."""

    received_at: datetime | None = None
    notes: str | None = None
    lines: list[PurchaseReceiptLineInput]


class PurchaseLineCloseInput(BaseModel):
    # True when the supplier billed for the whole line but shipped short, so the goods that
    # did arrive should carry the full charge.
    apportion_remainder: bool = False


class PurchaseLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    purchase_id: int
    material_id: int
    qty: Decimal
    total_cost: Decimal
    notes: str | None = None
    closed_at: datetime | None = None
    received_qty: Decimal = Decimal(0)
    outstanding_qty: Decimal = Decimal(0)
    receipts: list[PurchaseReceiptRead] = []


class MaterialStockHistoryRead(BaseModel):
    """A single row in a material's unified stock timeline, merged and ordered
    chronologically. Used by GET /materials/{id}/stock-history.

    Three kinds. 'purchase' is a delivery that happened, dated when it arrived, and those
    plus the 'adjustment' rows account for current_qty exactly. 'purchase_outstanding' is
    what is still on order — shown on the same timeline because that is where someone looks
    for it, but deliberately a different kind, because it has not moved any stock."""

    id: int
    kind: Literal["purchase", "purchase_outstanding", "adjustment"]
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
    expected_arrival_date: date | None = None
    status: PurchaseStatus
    received_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    lines: list[PurchaseLineRead] = []
