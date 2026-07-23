from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ShippingProfile(Base):
    """A named, reusable shipping method (e.g. "Small parcel — 2nd class") — products
    default to one of these instead of each carrying its own flat shipping number.

    price is what the customer is charged for postage in addition to the sale price —
    a single value, since that's what you decide to charge regardless of channel.

    cost is what the seller actually pays the carrier, split per channel
    (cost_etsy/cost_ebay/cost_manual) because the same physical shipping method can
    genuinely cost different amounts depending on where the label is bought from (e.g.
    Etsy's own shipping label purchase price vs. a manual/independent postage account).
    See services/shipping_profiles.py for the resolvers that pick the right one.
    """

    __tablename__ = "shipping_profiles"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_shipping_profiles_price_nonneg"),
        CheckConstraint("cost_etsy >= 0", name="ck_shipping_profiles_cost_etsy_nonneg"),
        CheckConstraint("cost_ebay >= 0", name="ck_shipping_profiles_cost_ebay_nonneg"),
        CheckConstraint("cost_manual >= 0", name="ck_shipping_profiles_cost_manual_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_etsy: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_ebay: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_manual: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
