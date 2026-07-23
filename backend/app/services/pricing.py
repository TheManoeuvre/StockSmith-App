from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import ProductPriceSnapshot
from app.models.product import Product, ProductMaterial
from app.services import platform_fees
from app.services.shipping_profiles import (
    get_shipping_profiles_by_id,
    resolve_product_shipping_profile,
    resolve_shipping_cost_for_fee_source,
)

# A material cost recompute only triggers a fresh snapshot if the product's cost_per_unit
# has drifted by more than this fraction since its last snapshot — otherwise every tiny
# weighted-average nudge would spam the price history with noise.
_DRIFT_THRESHOLD = Decimal("0.03")

# Dashboard margin alerts fire when the two most recent snapshots differ by more than this
# many percentage points.
_MARGIN_ALERT_THRESHOLD = Decimal("5")


def compute_profit_margin(
    sale_price: Decimal | None,
    cost_per_unit: Decimal | None,
    shipping_cost: Decimal | None,
    platform_fee_percent: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Returns (profit, margin_percent). Both None if sale_price isn't set — there's
    nothing meaningful to compute margin against."""
    if sale_price is None:
        return None, None
    fee = sale_price * (platform_fee_percent or Decimal(0)) / Decimal(100)
    profit = sale_price - (cost_per_unit or Decimal(0)) - (shipping_cost or Decimal(0)) - fee
    margin_percent = (profit / sale_price * Decimal(100)) if sale_price != 0 else None
    return profit, margin_percent


async def snapshot_product_pricing(session: AsyncSession, product: Product, cost_per_unit: Decimal) -> None:
    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    shipping_profile = resolve_product_shipping_profile(shipping_profiles_by_id, product)
    shipping_price = shipping_profile.price if shipping_profile else None
    shipping_cost = resolve_shipping_cost_for_fee_source(shipping_profile, fee_source) if shipping_profile else None
    effective_fee_percent = platform_fees.resolve_fee_percent(
        fee_source, fee_components, product.platform_fee_percent, product.sale_price, shipping_price
    )
    _, margin_percent = compute_profit_margin(
        product.sale_price, cost_per_unit, shipping_cost, effective_fee_percent
    )
    session.add(
        ProductPriceSnapshot(
            product_id=product.id,
            cost_per_unit=cost_per_unit,
            sale_price=product.sale_price,
            margin_percent=margin_percent,
        )
    )
    await session.flush()


async def check_and_snapshot_for_materials(session: AsyncSession, material_ids: set[int]) -> None:
    """Called after a material cost recompute — snapshots pricing for any product whose
    BOM includes one of the affected materials and whose cost_per_unit has drifted enough
    to be worth recording. Products with no sale_price set are skipped (nothing to alert on)."""
    if not material_ids:
        return

    product_ids_result = await session.execute(
        select(ProductMaterial.product_id).where(ProductMaterial.material_id.in_(material_ids)).distinct()
    )
    product_ids = [row[0] for row in product_ids_result]
    if not product_ids:
        return

    from app.services.buildability import get_cost_per_unit_by_product

    cost_per_unit_by_product = await get_cost_per_unit_by_product(session)

    products_result = await session.execute(
        select(Product).where(Product.id.in_(product_ids), Product.sale_price.is_not(None))
    )
    for product in products_result.scalars():
        cost_per_unit = cost_per_unit_by_product.get(product.id)
        if cost_per_unit is None:
            continue

        latest = (
            await session.execute(
                select(ProductPriceSnapshot)
                .where(ProductPriceSnapshot.product_id == product.id)
                .order_by(ProductPriceSnapshot.recorded_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if latest is None or latest.cost_per_unit == 0:
            drifted = latest is None or cost_per_unit != 0
        else:
            drifted = abs(cost_per_unit - Decimal(latest.cost_per_unit)) / Decimal(latest.cost_per_unit) > _DRIFT_THRESHOLD

        if drifted:
            await snapshot_product_pricing(session, product, cost_per_unit)


async def get_margin_alerts(session: AsyncSession) -> list[dict]:
    """Products whose two most-recent snapshots differ by more than the alert threshold."""
    result = await session.execute(
        select(Product.id, Product.name).join(ProductPriceSnapshot, ProductPriceSnapshot.product_id == Product.id).distinct()
    )
    alerts = []
    for product_id, name in result:
        snapshots = (
            await session.execute(
                select(ProductPriceSnapshot)
                .where(ProductPriceSnapshot.product_id == product_id)
                .order_by(ProductPriceSnapshot.recorded_at.desc())
                .limit(2)
            )
        ).scalars().all()
        if len(snapshots) < 2:
            continue
        current, previous = snapshots
        if current.margin_percent is None or previous.margin_percent is None:
            continue
        diff = Decimal(current.margin_percent) - Decimal(previous.margin_percent)
        if abs(diff) > _MARGIN_ALERT_THRESHOLD:
            alerts.append(
                {
                    "product_id": product_id,
                    "name": name,
                    "previous_margin_percent": Decimal(previous.margin_percent),
                    "current_margin_percent": Decimal(current.margin_percent),
                }
            )
    return alerts
