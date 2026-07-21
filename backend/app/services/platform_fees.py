from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.platform_fee import FeeBasis, MarginFeeConfig, MarginFeeSource, PlatformFeeComponent
from app.models.product import Product
from app.models.variant import ProductVariant


async def get_margin_fee_config(session: AsyncSession) -> MarginFeeConfig:
    config = await session.get(MarginFeeConfig, 1)
    if config is None:
        # Should only happen on a DB that predates the seeding migration somehow —
        # fall back to the same safe default the migration seeds.
        config = MarginFeeConfig(id=1, fee_source=MarginFeeSource.manual)
        session.add(config)
        await session.commit()
    return config


async def set_margin_fee_config(session: AsyncSession, fee_source: MarginFeeSource) -> MarginFeeConfig:
    config = await get_margin_fee_config(session)
    config.fee_source = fee_source
    await session.commit()
    return config


async def get_fee_components(session: AsyncSession, platform: ListingPlatform) -> list[PlatformFeeComponent]:
    result = await session.execute(
        select(PlatformFeeComponent)
        .where(PlatformFeeComponent.platform == platform)
        .order_by(PlatformFeeComponent.display_order)
    )
    return list(result.scalars())


def compute_effective_fee_amount(
    components: list[PlatformFeeComponent], sale_price: Decimal, shipping_cost: Decimal | None
) -> Decimal:
    """Walks enabled components in display_order, accumulating a running fee subtotal.
    Percentage components multiply their basis (sale price, or sale price + shipping);
    fees_subtotal-basis components (how "VAT on fees" is modeled) multiply the running
    subtotal itself rather than the sale price. Fixed components just add a flat amount.
    Returns the total fee amount, in the same currency unit as sale_price."""
    shipping = shipping_cost or Decimal(0)
    subtotal = Decimal(0)
    for component in sorted((c for c in components if c.enabled), key=lambda c: c.display_order):
        if component.basis == FeeBasis.fees_subtotal:
            if component.rate_percent:
                subtotal += subtotal * Decimal(component.rate_percent) / Decimal(100)
            continue
        basis_amount = sale_price if component.basis == FeeBasis.sale_price else sale_price + shipping
        if component.rate_percent:
            subtotal += basis_amount * Decimal(component.rate_percent) / Decimal(100)
        if component.fixed_amount:
            subtotal += Decimal(component.fixed_amount)
    return subtotal


async def compute_effective_fee_percent(
    session: AsyncSession, platform: ListingPlatform, sale_price: Decimal | None, shipping_cost: Decimal | None
) -> Decimal | None:
    """Resolves a calculated platform's fee components down to a single effective
    percent-of-sale-price, so callers that already work in terms of a flat fee % (the
    existing margin-calculation code) don't need to change shape."""
    if sale_price is None or sale_price == 0:
        return None
    components = await get_fee_components(session, platform)
    fee_amount = compute_effective_fee_amount(components, sale_price, shipping_cost)
    return fee_amount / sale_price * Decimal(100)


async def get_resolver_context(session: AsyncSession) -> tuple[MarginFeeSource, list[PlatformFeeComponent]]:
    """Fetches the global fee source + (if not manual) that platform's components once,
    so a list endpoint can resolve every product/variant's effective fee percent without
    a query per row."""
    config = await get_margin_fee_config(session)
    if config.fee_source == MarginFeeSource.manual:
        return config.fee_source, []
    components = await get_fee_components(session, ListingPlatform(config.fee_source.value))
    return config.fee_source, components


def resolve_fee_percent(
    fee_source: MarginFeeSource,
    components: list[PlatformFeeComponent],
    manual_fee_percent: Decimal | None,
    sale_price: Decimal | None,
    shipping_cost: Decimal | None,
) -> Decimal | None:
    """Synchronous resolution given a context already fetched via get_resolver_context —
    safe to call in a per-row loop without additional DB round trips."""
    if fee_source == MarginFeeSource.manual:
        return manual_fee_percent
    if sale_price is None or sale_price == 0:
        return None
    fee_amount = compute_effective_fee_amount(components, sale_price, shipping_cost)
    return fee_amount / sale_price * Decimal(100)


def resolve_variant_fee_percent(
    fee_source: MarginFeeSource,
    components: list[PlatformFeeComponent],
    variant: ProductVariant,
    product: Product | None,
) -> Decimal | None:
    """Same as resolve_fee_percent, but applies the variant-falls-back-to-product pattern
    already used for sale_price/shipping_cost/platform_fee_percent in pricing modes —
    a variant with no price/fee of its own inherits the product's."""
    manual_fee_percent = variant.platform_fee_percent if variant.platform_fee_percent is not None else (
        product.platform_fee_percent if product else None
    )
    sale_price = variant.sale_price if variant.sale_price is not None else (product.sale_price if product else None)
    shipping_cost = variant.shipping_cost if variant.shipping_cost is not None else (
        product.shipping_cost if product else None
    )
    return resolve_fee_percent(fee_source, components, manual_fee_percent, sale_price, shipping_cost)
