from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ShippingProfileBase(BaseModel):
    name: str
    price: Decimal = Decimal(0)
    # Per-channel buyer price; null means "use price". See ShippingProfile's docstring.
    price_etsy: Decimal | None = None
    price_ebay: Decimal | None = None
    cost_etsy: Decimal = Decimal(0)
    cost_ebay: Decimal = Decimal(0)
    cost_manual: Decimal = Decimal(0)
    # Marketplace link — the marketplace's own profile/policy id, one local profile each.
    etsy_shipping_profile_id: int | None = None
    ebay_fulfillment_policy_id: str | None = None


class ShippingProfileCreate(ShippingProfileBase):
    pass


class ShippingProfileUpdate(BaseModel):
    name: str | None = None
    price: Decimal | None = None
    price_etsy: Decimal | None = None
    price_ebay: Decimal | None = None
    cost_etsy: Decimal | None = None
    cost_ebay: Decimal | None = None
    cost_manual: Decimal | None = None
    etsy_shipping_profile_id: int | None = None
    ebay_fulfillment_policy_id: str | None = None
    is_archived: bool | None = None


class ShippingProfileRead(ShippingProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_archived: bool = False
    # How many products, variants and orders point at this. Computed per request.
    usage_count: int = 0
    created_at: datetime
    updated_at: datetime


class MarketplaceShippingProfileRead(BaseModel):
    """One Etsy shipping profile or eBay fulfillment policy as the link picker and the
    import see it. `domestic_price` is what the buyer is charged for the profile's own
    domestic destination; None when the marketplace calculates postage at checkout
    (nothing fixed to import) or when no destination could be read.

    `domestic_fallback` is set when the domestic destination could not be identified and
    the first destination was used instead, so the UI can say so rather than present a
    guess as a fact."""

    id: str
    title: str
    is_calculated: bool = False
    domestic_price: Decimal | None = None
    domestic_fallback: bool = False
