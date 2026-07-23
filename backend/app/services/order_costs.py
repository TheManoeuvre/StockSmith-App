from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.buildability import compute_variant_buildability, get_cost_per_unit_by_product
from app.services.kitting import compute_variant_kitting_cost_per_unit
from app.services.shipping_profiles import get_shipping_profiles_by_id, resolve_variant_shipping_profile


async def compute_line_cost_snapshot(
    session: AsyncSession, product_id: int | None, variant_id: int | None
) -> tuple[Decimal | None, Decimal | None]:
    """Resolves the build-BOM and kitting-BOM cost per unit for a product/variant, to be
    snapshotted onto an OrderLine once at creation time (see order_sync._upsert_lines and
    routers.orders.create_order) — frozen at that point, never recomputed later.

    product_id is None for a needs_mapping line that hasn't been matched to a product
    yet; there's nothing to cost in that case.
    """
    if product_id is None:
        return None, None

    if variant_id is not None:
        _, _, cost_per_unit, _ = await compute_variant_buildability(session, product_id, variant_id)
    else:
        cost_per_unit = (await get_cost_per_unit_by_product(session)).get(product_id)

    kitting_cost_per_unit = await compute_variant_kitting_cost_per_unit(session, product_id, variant_id)

    return cost_per_unit, kitting_cost_per_unit


async def resolve_order_shipping_profile(
    session: AsyncSession, lines: list[tuple[int | None, int | None]]
) -> int | None:
    """Picks a default shipping profile for a newly-created order from its lines' resolved
    product/variant default (variant falls back to product) — the first line that resolves
    to one wins. Used only to *default* Order.shipping_profile_id at creation/sync time;
    it's editable afterward and callers must not overwrite an already-set value with this.

    lines is a list of (product_id, variant_id) tuples — product_id may be None for a
    needs_mapping line that hasn't been matched to a product yet.
    """
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    for product_id, variant_id in lines:
        if product_id is None:
            continue
        product = await session.get(Product, product_id)
        variant = await session.get(ProductVariant, variant_id) if variant_id is not None else None
        profile = resolve_variant_shipping_profile(shipping_profiles_by_id, variant, product)
        if profile is not None:
            return profile.id
    return None
