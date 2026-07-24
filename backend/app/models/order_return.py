import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class ReturnDisposition(str, enum.Enum):
    scrap = "scrap"
    return_to_stock = "return_to_stock"


class ReturnSource(str, enum.Enum):
    cancel_before_ship = "cancel_before_ship"
    return_after_ship = "return_after_ship"


class ReturnScope(str, enum.Enum):
    # The finished product/variant unit itself.
    product = "product"
    # One kitting/packaging material consumed for this line's shipped units — material_id
    # is set only for this scope.
    kitting = "kitting"


class OrderLineReturn(Base):
    """Audit trail for every scrap/return-to-stock decision made while cancelling or
    processing a return on an order line — the record docs/plan-marketplace-
    integrations.md Section 4 calls "scrap/return history."

    One row per (line, scope) for product-scope decisions, and one row per (line, scope,
    material) for kitting-scope decisions — a shipped line's kitting footprint can span
    several materials (box, label, etc), each disposed of independently.

    Disposition meaning is scope- and source-dependent, not a uniform "credit or don't":
    - product / cancel_before_ship: the unit was only ever *reserved*, never physically
      consumed. return_to_stock is the do-nothing baseline (releasing the reservation
      already makes it sellable again); scrap is the extra step — the reservation is
      released AND current_stock is written down, because the physical unit is being
      declared gone even though it never shipped.
    - product / return_after_ship: the unit was *already* deducted from current_stock at
      ship time. return_to_stock is the extra step — current_stock is credited back up
      because a resellable unit physically arrived back; scrap is the do-nothing
      baseline — it stays deducted, matching a damaged/unsellable return.
    - kitting / return_after_ship (kitting has no cancel_before_ship case — packaging is
      only ever reserved, never consumed, pre-ship, and reservation release needs no
      per-line decision): the material was already deducted at ship time. scrap (the
      default) is the do-nothing baseline — packaging genuinely can't be un-consumed.
      return_to_stock is the override for when it demonstrably came back reusable.
    """

    __tablename__ = "order_line_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[ReturnScope] = mapped_column(portable_enum(ReturnScope, name="return_scope"), nullable=False)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="SET NULL"), nullable=True)
    # Numeric, not Integer — product-scope rows are always whole units, but kitting-scope
    # rows are qty_required * shipped_qty, and packaging BOM quantities can be fractional.
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    disposition: Mapped[ReturnDisposition] = mapped_column(
        portable_enum(ReturnDisposition, name="return_disposition"), nullable=False
    )
    source: Mapped[ReturnSource] = mapped_column(portable_enum(ReturnSource, name="return_source"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
