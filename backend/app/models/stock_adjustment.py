import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StockAdjustmentMode(str, enum.Enum):
    adjust = "adjust"
    set = "set"


class StockAdjustment(Base):
    """Manual stock correction for a product or one of its variants (physical recount,
    damage, etc) — the products/variants analog of MaterialAdjustment.

    Mirrors Build's ownership shape: product_id is always set, variant_id only when the
    adjustment targets a specific variant rather than the bare product. current_stock is
    a plain running counter here (not replayed from history like Material.current_qty),
    so this row is mutated directly against it rather than triggering any recompute.

    mode/target_qty exist purely for audit display, same as MaterialAdjustment: a "set"
    adjustment (a physical stock count) is stored as the plain delta needed to reach that
    count, with target_qty remembering the count itself. A "set" confirming the count
    already matches produces a zero delta, which is why the nonzero constraint is relaxed
    for mode='set'.
    """

    __tablename__ = "stock_adjustments"
    __table_args__ = (
        CheckConstraint("qty_delta != 0 OR mode = 'set'", name="ck_stock_adjustments_qty_delta_nonzero"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    mode: Mapped[StockAdjustmentMode] = mapped_column(
        PgEnum(StockAdjustmentMode, name="stock_adjustment_mode", create_type=False),
        nullable=False,
        default=StockAdjustmentMode.adjust,
    )
    qty_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    target_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
