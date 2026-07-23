from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.platform_fee import MarginFeeSource
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant


async def get_shipping_profiles_by_id(session: AsyncSession) -> dict[int, ShippingProfile]:
    """Fetches every shipping profile once, so a list endpoint can resolve every
    product/variant's effective profile without a query per row — same shape as
    platform_fees.get_resolver_context."""
    result = await session.execute(select(ShippingProfile))
    return {p.id: p for p in result.scalars()}


def resolve_product_shipping_profile(
    profiles_by_id: dict[int, ShippingProfile], product: Product | None
) -> ShippingProfile | None:
    if product is None or product.shipping_profile_id is None:
        return None
    return profiles_by_id.get(product.shipping_profile_id)


def resolve_variant_shipping_profile(
    profiles_by_id: dict[int, ShippingProfile],
    variant: ProductVariant | None,
    product: Product | None,
) -> ShippingProfile | None:
    """Variant falls back to product — same NULL-means-inherit convention already used
    for sale_price/platform_fee_percent (see resolve_variant_fee_percent)."""
    if variant is not None and variant.shipping_profile_id is not None:
        return profiles_by_id.get(variant.shipping_profile_id)
    return resolve_product_shipping_profile(profiles_by_id, product)


def resolve_shipping_cost_for_platform(profile: ShippingProfile, platform: ListingPlatform | None) -> Decimal:
    """Picks the right per-channel cost for an actual order — platform is None for a
    manual order. Used to snapshot Order.shipping_cost_snapshot at ship time (see
    services/allocation.ship_order): the real cost of shipping this order depends on
    which channel it actually shipped through, since e.g. Etsy's own label-purchase
    price for a method can differ from what the same method costs bought manually.
    Any platform without a dedicated cost column (only Etsy/eBay have one so far, per
    product decision — Shopify has no adapter yet) falls back to cost_manual."""
    if platform == ListingPlatform.etsy:
        return Decimal(profile.cost_etsy)
    if platform == ListingPlatform.ebay:
        return Decimal(profile.cost_ebay)
    return Decimal(profile.cost_manual)


def resolve_shipping_cost_for_fee_source(profile: ShippingProfile, fee_source: MarginFeeSource) -> Decimal:
    """Same per-channel cost pick, but for the product-level margin estimate — which
    has no real order to key off yet, so it uses the shop-wide "Margin fee source"
    switch (Settings -> Pricing) as a stand-in for "which channel am I estimating for."
    MarginFeeSource's manual/etsy/ebay values map 1:1 onto the three cost columns."""
    if fee_source == MarginFeeSource.etsy:
        return Decimal(profile.cost_etsy)
    if fee_source == MarginFeeSource.ebay:
        return Decimal(profile.cost_ebay)
    return Decimal(profile.cost_manual)
