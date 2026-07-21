import enum
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PurchaseStatus(str, enum.Enum):
    ordered = "ordered"
    received = "received"


class Purchase(Base):
    """A purchase order from a supplier, covering one or more materials at once.

    Recording a purchase does not affect stock/cost until it's marked received —
    see services/costing.py's recompute_material for how received purchases (and
    material_adjustments) get replayed to derive current_qty/avg_unit_cost.
    """

    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    order_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    status: Mapped[PurchaseStatus] = mapped_column(
        PgEnum(PurchaseStatus, name="purchase_status", create_type=False),
        nullable=False,
        default=PurchaseStatus.ordered,
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["MaterialPurchase"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")
    supplier: Mapped["Supplier | None"] = relationship()

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.name if self.supplier else None


class MaterialPurchase(Base):
    """A single material line item within a Purchase order."""

    __tablename__ = "material_purchases"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_material_purchases_qty_positive"),
        CheckConstraint("total_cost >= 0", name="ck_material_purchases_total_cost_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    total_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    purchase: Mapped["Purchase"] = relationship(back_populates="lines")
    material: Mapped["Material"] = relationship(back_populates="purchase_lines")
