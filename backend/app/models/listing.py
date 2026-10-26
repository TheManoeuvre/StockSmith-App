import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, column, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class ListingPlatform(str, enum.Enum):
    etsy = "etsy"
    ebay = "ebay"
    shopify = "shopify"
    square = "square"


class Listing(Base):
    """Per-product/variant marketplace listing state: one primary row per (product/variant,
    platform), plus one additional row for each further listing sharing the SKU. external_title/external_state/external_quantity/last_checked_at are
    populated by the Stage 1 SKU sync-verification check (services/listing_sync.py) — a
    read-only "does this SKU match a real listing" test. last_pushed_qty/last_pushed_at
    are the outbound quantity-push watermark (services/listing_push.py); last_synced_qty/
    last_synced_at predate them and are now display/bookkeeping only."""

    __tablename__ = "listings"
    __table_args__ = (
        # One *primary* row per (product/variant, platform) — a NULL variant_id means "the
        # product itself has no variants". Coalesced so two no-variant rows for the same
        # product collide instead of being treated as distinct (plain UNIQUE would let
        # NULLs duplicate freely). Partial on is_primary because a unit can also carry
        # additional rows, one per further marketplace listing that holds the same SKU
        # (A/B-test and free-shipping variants sharing one stock pool).
        Index(
            "uq_listings_product_variant_platform",
            "product_id",
            func.coalesce(column("variant_id"), -1),
            "platform",
            unique=True,
            sqlite_where=column("is_primary") == true(),
            postgresql_where=column("is_primary") == true(),
        ),
        # A marketplace listing can be linked to a given unit once, primary or not.
        Index(
            "uq_listings_unit_platform_external_listing",
            "product_id",
            func.coalesce(column("variant_id"), -1),
            "platform",
            "external_listing_id",
            unique=True,
            sqlite_where=column("external_listing_id").is_not(None),
            postgresql_where=column("external_listing_id").is_not(None),
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
    # False for the second and later marketplace listings that hold this unit's SKU. The
    # primary row is the one every single-listing code path reads (status badges, ceiling
    # bookkeeping, draft linking); quantity pushes go to every linked row. Maintained by
    # listing_sync.check_product_sku_sync, which is the only place rows are linked.
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    # Free-text seller label for telling listings of one unit apart ("free shipping",
    # "test B"). Display only.
    role: Mapped[str | None] = mapped_column(String, nullable=True)
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
    external_title: Mapped[str | None] = mapped_column(String, nullable=True)
    external_variation: Mapped[str | None] = mapped_column(String, nullable=True)
    external_state: Mapped[str | None] = mapped_column(String, nullable=True)
    external_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
