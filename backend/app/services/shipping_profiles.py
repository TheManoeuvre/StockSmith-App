from decimal import Decimal

from sqlalchemy import select, text
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


_ACTIVE_VARIANT_PROFILE_COVERAGE_SQL = text(
    """
    SELECT product_id,
           COUNT(*) AS active_count,
           SUM(CASE WHEN shipping_profile_id IS NULL THEN 1 ELSE 0 END) AS uncovered_count
    FROM product_variants
    WHERE is_active = true
    GROUP BY product_id
    """
)


async def get_active_variant_profile_coverage_by_product(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """(active variant count, how many of them carry no shipping profile of their own) per
    product, catalogue-wide in one query.

    Needed to tell a genuine gap from a false alarm. resolve_variant_shipping_profile checks
    the variant before falling back to the product, and order_costs.resolve_order_shipping_
    profile goes through it — so a product with no profile of its own whose every active
    variant sets one is fully covered, and flagging it would be wrong. Only a product with
    no profile AND at least one uncovered sellable path can actually ship without a postage
    cost."""
    rows = await session.execute(_ACTIVE_VARIANT_PROFILE_COVERAGE_SQL)
    return {row.product_id: (int(row.active_count), int(row.uncovered_count)) for row in rows}


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


def resolve_shipping_price_for_platform(profile: ShippingProfile, platform: ListingPlatform | None) -> Decimal:
    """The postage price the buyer is charged on a given channel — price_<platform> when
    set, else the manual/default `price`. The per-channel mirror of
    resolve_shipping_cost_for_platform: the same physical service is genuinely sold at
    different postage prices on different marketplaces, and once a profile is linked the
    marketplace's own figure is imported into price_<platform> (see
    services/shipping_price_sync.py). platform None is a manual order."""
    if platform == ListingPlatform.etsy and profile.price_etsy is not None:
        return Decimal(profile.price_etsy)
    if platform == ListingPlatform.ebay and profile.price_ebay is not None:
        return Decimal(profile.price_ebay)
    return Decimal(profile.price)


def resolve_shipping_price_for_fee_source(profile: ShippingProfile, fee_source: MarginFeeSource) -> Decimal:
    """Same per-channel price pick keyed off the shop-wide margin fee source, for the
    product-level margin estimate. Used everywhere the buyer-charged postage feeds margin
    or fee calculation (pricing, platform_fees, routers/products) so a linked profile's
    Etsy price moves the Etsy margin and nothing else."""
    if fee_source == MarginFeeSource.etsy:
        return resolve_shipping_price_for_platform(profile, ListingPlatform.etsy)
    if fee_source == MarginFeeSource.ebay:
        return resolve_shipping_price_for_platform(profile, ListingPlatform.ebay)
    return Decimal(profile.price)
