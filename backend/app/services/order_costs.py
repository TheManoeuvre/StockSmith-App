from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.buildability import compute_variant_buildability, get_cost_per_unit_by_product
from app.services.kitting import compute_variant_kitting_cost_per_unit


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
