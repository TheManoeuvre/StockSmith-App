from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Build(Base):
    """A finished-goods production event: qty_built units of a product (or one of its
    variants) were physically built just now, consuming materials per the resolved BOM.

    Atomic and immediate — unlike Purchase, there's no ordered/received lifecycle, since
    a build represents work already done. One product/variant per row; building several
    products at once is just several Build rows.
    """

    __tablename__ = "builds"
    __table_args__ = (CheckConstraint("qty_built > 0", name="ck_builds_qty_built_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True)
    qty_built: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
