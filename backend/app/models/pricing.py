from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductPriceSnapshot(Base):
    """Append-only history log of a product's cost/price/margin at a point in time.

    Not a derived/cached table like materials' current_qty — there's no "current" row to
    replay from, each insert is a permanent historical record. Written opportunistically
    whenever pricing fields change or a material cost recompute drifts a product's
    cost_per_unit by more than a threshold (see services/pricing.py).
    """

    __tablename__ = "product_price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    cost_per_unit: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    margin_percent: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
