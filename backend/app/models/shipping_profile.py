from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ShippingProfile(Base):
    """A named, reusable shipping method (e.g. "Small parcel — 2nd class") — products
    default to one of these instead of each carrying its own flat shipping number.

    price is what the customer is charged for postage in addition to the sale price. It is
    split per channel the same way cost is: `price` is the manual/default figure, and
    price_etsy / price_ebay override it for that marketplace when set. The same physical
    service genuinely sells at different postage prices on different marketplaces — Etsy's
    and eBay's profile editors set them independently — and a single field would force the
    marketplace refresh below to either overwrite or ignore one channel. Product margin
    counts this price as revenue (services/pricing.compute_profit_margin), which is why it
    has to be right per channel. See resolve_shipping_price_for_fee_source.

    cost is what the seller actually pays the carrier, split per channel
    (cost_etsy/cost_ebay/cost_manual) because the same physical shipping method can
    genuinely cost different amounts depending on where the label is bought from (e.g.
    Etsy's own shipping label purchase price vs. a manual/independent postage account).
    See services/shipping_profiles.py for the resolvers that pick the right one.

    Marketplace link. etsy_shipping_profile_id / ebay_fulfillment_policy_id tie this
    local profile to one Etsy shipping profile and/or one eBay fulfillment (postage)
    policy — unique per platform, so one local profile stands for one marketplace profile.
    Once linked, the marketplace is the source of truth for the buyer price: the import
    action and the scheduled refresh (services/shipping_price_sync.py) write
    price_<platform> from what the marketplace charges the buyer for its domestic
    destination, and never touch `price` or any cost_* (the marketplace knows nothing about
    what the carrier charges the seller). Linking alone imports nothing. The link also makes
    the product's shipping profile the single source of "how this ships" for draft
    listings: services/draft_listing.py takes the marketplace profile id from here before
    falling back to ListingProfile's own shipping fields. Archived profiles keep their link.

    Out of scope for the import, deliberately: Etsy's secondary_cost (the combined-shipping
    price for each additional item in the same order) and shipping_profile_upgrades
    (express options), and eBay's additional-item cost and non-first shipping services.
    Margin is a per-unit, single-item estimate, so the primary domestic cost is the number
    that belongs here. A calculated profile (Etsy profile_type "calculated", eBay costType
    "CALCULATED") has no fixed price at all and imports nothing.
    """

    __tablename__ = "shipping_profiles"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_shipping_profiles_price_nonneg"),
        CheckConstraint("cost_etsy >= 0", name="ck_shipping_profiles_cost_etsy_nonneg"),
        CheckConstraint("cost_ebay >= 0", name="ck_shipping_profiles_cost_ebay_nonneg"),
        CheckConstraint("cost_manual >= 0", name="ck_shipping_profiles_cost_manual_nonneg"),
        CheckConstraint("price_etsy IS NULL OR price_etsy >= 0", name="ck_shipping_profiles_price_etsy_nonneg"),
        CheckConstraint("price_ebay IS NULL OR price_ebay >= 0", name="ck_shipping_profiles_price_ebay_nonneg"),
        # One local profile per marketplace profile. Unique indexes rather than constraints
        # so NULLs (the unlinked majority) don't collide — both SQLite and Postgres treat
        # NULLs as distinct in a unique index.
        Index("uq_shipping_profiles_etsy_shipping_profile_id", "etsy_shipping_profile_id", unique=True),
        Index("uq_shipping_profiles_ebay_fulfillment_policy_id", "ebay_fulfillment_policy_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    # Per-channel buyer price; NULL means "use price". Written by the marketplace import and
    # refresh once linked, editable by hand otherwise.
    price_etsy: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    price_ebay: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    cost_etsy: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_ebay: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_manual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    # Etsy's shipping_profile_id is numeric; eBay's fulfillmentPolicyId is an opaque string.
    etsy_shipping_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ebay_fulfillment_policy_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Archived profiles disappear from every picker but keep resolving for rows that already
    # point at them. Needed because orders reference a profile too: deleting one would rewrite
    # what a historical order was shipped under, and merging it into another would do the same.
    # Retiring a profile you no longer offer is the actual intent, and it has to be expressible
    # without touching the past.
    is_archived: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

