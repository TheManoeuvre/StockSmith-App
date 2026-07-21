from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.listing import ListingPlatform


class SkuAlias(Base):
    """Remembers that a marketplace's raw SKU string refers to a specific StockSmith
    product/variant, even though it doesn't match Product.sku or the computed full SKU.

    Created automatically when a user maps an order line to a product/variant that
    already has a different SKU of its own (see routers/orders.py's _remember_sku) —
    so future orders using the same external SKU match automatically instead of needing
    the same line mapped by hand every time."""

    __tablename__ = "sku_aliases"
    __table_args__ = (UniqueConstraint("platform", "external_sku", name="uq_sku_aliases_platform_external_sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        PgEnum(ListingPlatform, name="listing_platform", create_type=False), nullable=False
    )
    external_sku: Mapped[str] = mapped_column(String, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
