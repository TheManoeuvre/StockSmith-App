import enum
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class FeeBasis(str, enum.Enum):
    sale_price = "sale_price"
    sale_price_plus_shipping = "sale_price_plus_shipping"
    # A % applied to the running fee subtotal accumulated so far (in display_order),
    # not to the sale price — this is how "VAT charged on top of platform fees" is
    # modeled, since VAT is a tax on the fees themselves, not on the sale.
    fees_subtotal = "fees_subtotal"


class PlatformFeeComponent(Base):
    """One named line item in a platform's real-world fee structure (e.g. Etsy's
    "Transaction fee" or "VAT on fees"). A product's effective margin fee is the sum of
    all enabled components for the active platform, applied in display_order — see
    services/platform_fees.py:compute_effective_fee_amount.

    Seeded with researched Etsy UK / eBay UK rates as of mid-2026 (see migration
    e_something_platform_fee_components) — these should be periodically re-verified
    against each platform's own fee pages, since rates change over time.
    """

    __tablename__ = "platform_fee_components"
    __table_args__ = (
        CheckConstraint(
            "rate_percent IS NOT NULL OR fixed_amount IS NOT NULL", name="ck_platform_fee_components_has_value"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    basis: Mapped[FeeBasis] = mapped_column(portable_enum(FeeBasis, name="fee_basis"), nullable=False)
    rate_percent: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    fixed_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarginFeeSource(str, enum.Enum):
    manual = "manual"
    etsy = "etsy"
    ebay = "ebay"


class MarginFeeConfig(Base):
    """Single-row (id=1) global setting: which fee model every product's margin
    calculation uses. 'manual' preserves today's flat Product/Variant.platform_fee_percent
    behaviour exactly; 'etsy'/'ebay' switch every product to the calculated fee from
    PlatformFeeComponent rows for that platform."""

    __tablename__ = "margin_fee_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    fee_source: Mapped[MarginFeeSource] = mapped_column(
        portable_enum(MarginFeeSource, name="margin_fee_source"),
        nullable=False,
        default=MarginFeeSource.manual,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
