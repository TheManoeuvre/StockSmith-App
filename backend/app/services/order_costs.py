from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant
from app.services.buildability import compute_variant_buildability, get_cost_per_unit_by_product
from app.services.shipping_profiles import (
    get_shipping_profiles_by_id,
    resolve_shipping_cost_for_platform,
    resolve_variant_shipping_profile,
)


async def compute_line_cost_snapshot(
    session: AsyncSession, product_id: int | None, variant_id: int | None
) -> Decimal | None:
    """Resolves the build-BOM cost per unit for a product/variant, to be snapshotted onto an
    OrderLine at its first allocation (see allocation._allocate_line) — frozen at that point,
    never recomputed later.

    Build BOM only. Packaging used to be resolved here too and snapshotted alongside as a
    per-unit rate, which systematically over-charged multi-unit orders: packaging is consumed
    once per ORDER (auto_apply_multiunit_kitting_override), so its cost now lives on the
    order's kitting ledger — see kitting.get_kitting_cogs_by_order.

    product_id is None for a needs_mapping line that hasn't been matched to a product
    yet; there's nothing to cost in that case.
    """
    if product_id is None:
        return None

    if variant_id is not None:
        _, _, cost_per_unit, _ = await compute_variant_buildability(session, product_id, variant_id)
    else:
        cost_per_unit = (await get_cost_per_unit_by_product(session)).get(product_id)

    return cost_per_unit


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


async def default_order_shipping_profile(session: AsyncSession, order: Order) -> None:
    """Auto-defaults an order's shipping profile from its lines' resolved product/variant
    default, and — only if the order has somehow already shipped without one — freezes the
    cost that ship_order would have frozen.

    Never overwrites an already-set profile, so a user's manual reassignment survives both
    a re-sync and any later allocation.

    Called from allocation.allocate_order rather than from each caller, because "the lines
    just changed, re-resolve" is exactly what every one of those callers means: a sync
    importing lines, map_sku/create_product_and_map attaching a product to a line that had
    none, create_order, and the manual /allocate endpoint. Doing it per-endpoint is how the
    gap arose in the first place — a needs_mapping line has product_id NULL, so it resolves
    to nothing at import, and nothing retried once the user mapped it. The order then
    shipped with no profile and therefore no postage cost, silently reading as £0 profit
    cost in _compute_net_profit.

    The already-shipped branch is deliberately the one place a cost is frozen outside
    ship_order. It can only fire when the snapshot was never taken at all, so it can't
    overwrite a frozen value with a newer price — and without it, an order that gains a
    profile after shipping (a later sync re-running this once the product was configured)
    would display a profile name against a blank postage cost forever: routers/orders.py's
    update_order refuses shipping changes on a shipped order, so the user can't fix it either.
    """
    if order.shipping_profile_id is not None:
        return
    await session.flush()
    result = await session.execute(
        select(OrderLine.product_id, OrderLine.variant_id).where(OrderLine.order_id == order.id)
    )
    pairs = [(product_id, variant_id) for product_id, variant_id in result]
    order.shipping_profile_id = await resolve_order_shipping_profile(session, pairs)

    if order.shipping_profile_id is None or order.shipping_cost_snapshot is not None:
        return
    if order.status != OrderStatus.shipped:
        return
    profile = await session.get(ShippingProfile, order.shipping_profile_id)
    if profile is not None:
        order.shipping_cost_snapshot = resolve_shipping_cost_for_platform(profile, order.platform)
