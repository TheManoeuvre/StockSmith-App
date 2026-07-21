import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, column, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ListingPlatform(str, enum.Enum):
    etsy = "etsy"
    ebay = "ebay"
    shopify = "shopify"


class Listing(Base):
    """Per-product/variant marketplace listing state, one row per (product/variant,
    platform). external_title/external_state/external_quantity/last_checked_at are
    populated by the Stage 1 SKU sync-verification check (services/listing_sync.py) — a
    read-only "does this SKU match a real listing" test. ceiling_qty/last_synced_qty/
    last_synced_at remain reserved for the future quantity-push phase (Stage 3+), which
    is not implemented yet."""

    __tablename__ = "listings"
    __table_args__ = (
        # One row per (product/variant, platform) — a NULL variant_id means "the product
        # itself has no variants". Coalesced so two no-variant rows for the same product
        # collide instead of being treated as distinct (plain UNIQUE would let NULLs
        # duplicate freely).
        Index(
            "uq_listings_product_variant_platform",
            "product_id",
            func.coalesce(column("variant_id"), -1),
            "platform",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    platform: Mapped[ListingPlatform] = mapped_column(
        PgEnum(ListingPlatform, name="listing_platform", create_type=False), nullable=False
    )
    external_listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ceiling_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_title: Mapped[str | None] = mapped_column(String, nullable=True)
    external_variation: Mapped[str | None] = mapped_column(String, nullable=True)
    external_state: Mapped[str | None] = mapped_column(String, nullable=True)
    external_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
