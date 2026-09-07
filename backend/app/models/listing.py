import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, column, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class ListingPlatform(str, enum.Enum):
    etsy = "etsy"
    ebay = "ebay"
    shopify = "shopify"


class Listing(Base):
    """Per-product/variant marketplace listing state, one row per (product/variant,
    platform). external_title/external_state/external_quantity/last_checked_at are
    populated by the Stage 1 SKU sync-verification check (services/listing_sync.py) — a
    read-only "does this SKU match a real listing" test. last_pushed_qty/last_pushed_at
    are the outbound quantity-push watermark (services/listing_push.py); last_synced_qty/
    last_synced_at predate them and are now display/bookkeeping only."""

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
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    external_listing_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # The SKU this marketplace has actually acknowledged, as distinct from the one
    # StockSmith would compute today. Needed because external_listing_id means the
    # listing id on Etsy and the SKU on eBay, and because re-deriving the SKU to ask
    # "what is live?" asks the wrong question — the derived value is the thing about to
    # change. Written wherever a marketplace confirms a SKU; see sku_generation.
    published_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    ceiling_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The quantity StockSmith last successfully sent to this marketplace, and when.
    # Distinct from last_synced_qty, which services/kitting.sync_listing_ceiling_qty also
    # writes with a different meaning (expected-max-sellable bookkeeping) — these two are
    # written ONLY by services/listing_push on a confirmed push and are the authority for
    # "has the number we'd send actually changed since last time", which is what lets the
    # push fan-out skip the no-op GET+PUT that dominated the 2026-09-07 API-budget blowout.
    last_pushed_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Non-NULL when a quantity push can NEVER succeed with the listing configured as it is
    # on the marketplace — currently only the Etsy "quantity doesn't vary by variation"
    # case (services/platforms/etsy.push_listing_quantity detects it from the GET it
    # already does). The value is a human-readable sentence naming the fix. While set:
    # listing_push._push_now skips the listing outright (no GET, no PUT), the reconcile
    # sweep's normal selection skips it, and sync_status._failing_push_counts leaves it
    # out of the menu-bar badge — a structural block is something the user must fix on the
    # marketplace, not a retry that will eventually clear. structural_push_block_at paces
    # the reconcile sweep's slow re-probe (a fixed listing clears the marker on the next
    # successful push). Cleared by services/listing_push._push_one on any confirmed push.
    structural_push_block: Mapped[str | None] = mapped_column(String, nullable=True)
    structural_push_block_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_title: Mapped[str | None] = mapped_column(String, nullable=True)
    external_variation: Mapped[str | None] = mapped_column(String, nullable=True)
    external_state: Mapped[str | None] = mapped_column(String, nullable=True)
    external_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
