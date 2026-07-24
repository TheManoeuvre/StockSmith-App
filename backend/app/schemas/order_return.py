from decimal import Decimal

from pydantic import BaseModel

from app.models.order_return import ReturnDisposition


class CancellationKittingMaterial(BaseModel):
    material_id: int
    material_name: str
    qty_per_unit: Decimal


class CancellationLineOption(BaseModel):
    order_line_id: int
    product_id: int | None
    variant_id: int | None
    product_name: str | None
    variant_name: str | None
    # Allocated-but-not-yet-shipped — released as part of cancelling regardless of
    # disposition; disposition only decides whether current_stock also gets written down.
    pending_qty: int
    # Already left the building — disposition decides whether current_stock gets
    # credited back up. 0 for a line with nothing shipped yet.
    shipped_qty: int
    default_product_disposition: ReturnDisposition
    # Only populated when shipped_qty > 0 — a not-yet-shipped line has no kitting
    # materials to dispose of (packaging is applied at ship time, never before).
    kitting_materials: list[CancellationKittingMaterial]
    default_kitting_disposition: ReturnDisposition


class CancellationPreview(BaseModel):
    order_id: int
    already_cancelled: bool
    lines: list[CancellationLineOption]


class LineCancellationDecision(BaseModel):
    order_line_id: int
    product_disposition: ReturnDisposition
    # Required (validated in the service) when the line has shipped_qty > 0; ignored
    # otherwise, since there's nothing kitting-related to dispose of pre-ship.
    kitting_disposition: ReturnDisposition | None = None


class OrderCancelRequest(BaseModel):
    line_decisions: list[LineCancellationDecision]
    reason: str | None = None
