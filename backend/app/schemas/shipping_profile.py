from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.listing import ListingPlatform
from app.models.shipping_profile import PriceEventSource


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


class ShippingProfilePriceEventRead(BaseModel):
    """One change to a per-channel buyer price — the "why did margin move" trail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    shipping_profile_id: int
    platform: ListingPlatform
    old_price: Decimal | None
    new_price: Decimal | None
    source: PriceEventSource
    changed_at: datetime


class PriceRefreshChange(BaseModel):
    shipping_profile_id: int
    name: str
    old_price: Decimal | None
    new_price: Decimal | None


class PriceRefreshResult(BaseModel):
    """What one refresh of a platform's linked shipping profiles did. `error` is set when
    the marketplace couldn't be read and nothing was written."""

    platform: ListingPlatform
    refreshed_at: datetime | None
    changed: list[PriceRefreshChange]
    unchanged_count: int
    skipped_calculated: list[str]
    missing_upstream: list[str]
    error: str | None = None


class PriceRefreshStatus(BaseModel):
    """Per platform: whether a refresh can run, when it last did, and how often."""

    platform: ListingPlatform
    connected: bool
    linked_count: int
    shipping_price_refresh_hours: int | None
    last_shipping_price_refresh_at: datetime | None


class PriceRefreshSettingsUpdate(BaseModel):
    shipping_price_refresh_hours: int


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
