from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, column, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class ListingProfile(Base):
    """A named, reusable bundle of the marketplace metadata a listing needs but inventory
    doesn't — taxonomy, policies, who made it, how long it takes to make.

    Modelled as a bundle rather than as a shop default plus per-product override columns
    because products that differ tend to differ *together*: a different taxonomy usually
    arrives with a different shipping profile and a different processing time. Picking one
    profile makes an incoherent half-overridden combination unrepresentable, and changing
    the shipping profile across twenty products is one edit instead of twenty.

    The same shape the app already uses for ShippingProfile: a named row referenced by FK,
    not a pile of nullable columns on Product.

    Every field is nullable and nothing is seeded. A wrong fulfillment policy id doesn't
    fail loudly — it silently mis-ships — so an empty field that blocks a draft with a
    clear message is strictly better than a plausible guess. See services/draft_readiness.py.

    One table covers both marketplaces, with the platform on the row, because the concepts
    don't map onto each other: Etsy's taxonomy_id and eBay's categoryId are different
    things, so sharing a column would only hide that.
    """

    __tablename__ = "listing_profiles"
    __table_args__ = (
        UniqueConstraint("platform", "name", name="uq_listing_profiles_platform_name"),
        # At most one default per platform. Partial index rather than a plain unique on
        # (platform, is_default): every non-default row has is_default=false, and a plain
        # constraint would let only one of those exist.
        Index(
            "uq_listing_profiles_platform_default",
            "platform",
            unique=True,
            sqlite_where=column("is_default").is_(True),
            postgresql_where=column("is_default").is_(True),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Etsy ---
    etsy_taxonomy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Etsy enums, stored as their wire values ("i_did", "made_to_order") rather than as a
    # local enum: they are Etsy's vocabulary, they change on Etsy's schedule, and a new
    # value should not need a migration here.
    etsy_who_made: Mapped[str | None] = mapped_column(String, nullable=True)
    etsy_when_made: Mapped[str | None] = mapped_column(String, nullable=True)
    etsy_is_supply: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Etsy's own shipping profile id, which is not StockSmith's ShippingProfile — that one
    # is a local price/cost concept referenced by historical orders and carries no
    # marketplace id.
    etsy_shipping_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Etsy's processing profile. createDraftListing's own docs list this as optional, but
    # the live API refuses a physical draft without it ("A readiness_state_id is required
    # for physical listings"), so it is required-in-practice here too.
    etsy_readiness_state_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etsy_return_policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etsy_shop_section_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etsy_processing_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    etsy_processing_max: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- eBay ---
    ebay_category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_condition: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_fulfillment_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_payment_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_return_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_merchant_location_key: Mapped[str | None] = mapped_column(String, nullable=True)
    ebay_marketplace_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductPlatformSettings(Base):
    """Per-product, per-platform listing settings: which profile to use, whether the
    product is aimed at this platform at all, and platform-specific listing copy.

    Separate from Product because these are per-platform facts and Product is the app's
    busiest table — ten nullable marketplace columns there would not survive a third
    platform.

    `is_target` is written now and read later: services/product_platforms.target_platforms
    currently derives the target set from connections and live listings, and will honour
    this when a row exists. Adding the column with the table rather than in its own
    migration keeps that a one-line change rather than a schema change.
    """

    __tablename__ = "product_platform_settings"
    __table_args__ = (
        UniqueConstraint("product_id", "platform", name="uq_product_platform_settings_product_platform"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    # Null means "use the platform's default profile".
    listing_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing_profiles.id", ondelete="SET NULL"), nullable=True
    )
    # Null means "decide from connections and live listings", which is today's behaviour
    # for every product. Only an explicit false excludes a platform.
    is_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Platform-specific listing copy, overriding Product.listing_title/listing_description.
    # Earns its place on the numbers: Etsy allows 140 title characters and eBay 80, so a
    # title tuned for Etsy's budget cannot also fit eBay at the top end.
    listing_title: Mapped[str | None] = mapped_column(String, nullable=True)
    listing_description: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
