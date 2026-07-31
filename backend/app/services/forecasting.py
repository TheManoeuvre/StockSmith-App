"""Materials Weeks-of-Supply forecast — MRP-style dependent demand.

Independent demand (product/variant sales rate) is exploded through each product's
build BOM and kitting BOM to get dependent demand per material, aggregated across every
product/variant that consumes it. Coverage is a time-phased (piecewise) calculation: a
product's own finished-goods stock delays its draw on a material, and on-order purchase
lines are timed inflows — both are just breakpoints on the same timeline, not a single
on-hand-divided-by-rate figure. See docs/plan-materials-forecasting context in the PR
description for the worked example this generalizes.

Deliberately in-process/Python rather than one large SQL query: at this shop's scale
(dozens of products/materials) fetching the small amount of raw history per lookback
window and aggregating in Python is simpler to get right than replicating this logic in
SQL across two DB backends (SQLite dev, Postgres prod), and stays fast (see
compute_dashboard_summary, which this feeds into on every dashboard load).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build, BuildFailedConsumption
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import ProductMaterial
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.services.buildability import get_resolved_variant_bom
from app.services.general_settings import get_general_settings
from app.services.kitting import get_resolved_kitting_bom

_MIN_ACTIVE_WEEKS_FOR_FORECAST = 2

_MATERIALS_SQL = text(
    """
    SELECT m.id, m.name, m.current_qty, m.allocated_qty, m.reorder_threshold,
           m.default_supplier_id, s.name AS supplier_name
    FROM materials m
    LEFT JOIN suppliers s ON s.id = m.default_supplier_id
    WHERE m.is_active = true
    """
)

_ACTIVE_VARIANTS_SQL = text(
    "SELECT id, product_id, current_stock, allocated_qty FROM product_variants WHERE is_active = true"
)

_PRODUCTS_WITHOUT_ACTIVE_VARIANTS_SQL = text(
    """
    SELECT id, current_stock, allocated_qty FROM products
    WHERE is_active = true AND id NOT IN (SELECT product_id FROM product_variants WHERE is_active = true)
    """
)


@dataclass
class MaterialForecast:
    material_id: int
    name: str
    current_qty: Decimal
    allocated_qty: Decimal
    on_order_qty: Decimal
    reorder_threshold: Decimal
    supplier_id: int | None
    supplier_name: str | None
    consumption_rate_per_week: Decimal | None
    weeks_of_supply: Decimal | None
    fg_buffer_weeks: Decimal | None
    status: str  # "critical" | "warning" | "insufficient_data"


@dataclass
class _Entity:
    product_id: int
    variant_id: int | None
    current_stock: Decimal
    allocated_qty: Decimal


@dataclass
class _DemandPiece:
    # Weeks from now before this contribution starts drawing on the material (a build-BOM
    # line waits out its product's finished-goods buffer; a kitting-BOM line is 0 — packaging
    # is consumed at ship time, not delayed by finished-goods stock).
    buffer_weeks: Decimal
    rate: Decimal  # units/week drawn once buffer_weeks has elapsed


def _week_bucket(at: datetime, now: datetime) -> int:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (now - at).days // 7


async def _get_sellable_entities(session: AsyncSession) -> list[_Entity]:
    variant_rows = await session.execute(_ACTIVE_VARIANTS_SQL)
    entities = [
        _Entity(
            product_id=row.product_id,
            variant_id=row.id,
            current_stock=Decimal(row.current_stock),
            allocated_qty=Decimal(row.allocated_qty),
        )
        for row in variant_rows
    ]
    product_rows = await session.execute(_PRODUCTS_WITHOUT_ACTIVE_VARIANTS_SQL)
    entities += [
        _Entity(
            product_id=row.id,
            variant_id=None,
            current_stock=Decimal(row.current_stock),
            allocated_qty=Decimal(row.allocated_qty),
        )
        for row in product_rows
    ]
    return entities


async def _get_sales_by_entity(
    session: AsyncSession, cutoff: datetime, now: datetime
) -> dict[tuple[int, int | None], tuple[Decimal, set[int]]]:
    result = await session.execute(
        select(OrderLine.product_id, OrderLine.variant_id, OrderLine.ordered_qty, Order.order_placed_at)
        .join(Order, Order.id == OrderLine.order_id)
        .where(
            Order.order_placed_at >= cutoff,
            Order.status != OrderStatus.cancelled,
            OrderLine.product_id.is_not(None),
        )
    )
    totals: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: Decimal(0))
    weeks: dict[tuple[int, int | None], set[int]] = defaultdict(set)
    for product_id, variant_id, ordered_qty, order_placed_at in result:
        key = (product_id, variant_id)
        totals[key] += Decimal(ordered_qty)
        weeks[key].add(_week_bucket(order_placed_at, now))
    return {key: (totals[key], weeks[key]) for key in totals}


async def _get_builds_by_entity(
    session: AsyncSession, cutoff: datetime
) -> dict[tuple[int, int | None], tuple[Decimal, Decimal]]:
    result = await session.execute(
        select(Build.product_id, Build.variant_id, Build.qty_built, Build.qty_failed).where(Build.built_at >= cutoff)
    )
    built: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: Decimal(0))
    failed: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: Decimal(0))
    for product_id, variant_id, qty_built, qty_failed in result:
        key = (product_id, variant_id)
        built[key] += Decimal(qty_built)
        failed[key] += Decimal(qty_failed)
    return {key: (built[key], failed[key]) for key in built}


async def _get_scrap_by_entity_material(
    session: AsyncSession, cutoff: datetime
) -> dict[tuple[int, int | None], dict[int, Decimal]]:
    """Per (product/variant, material), how much was actually consumed by failed builds
    in the window — the real, per-material scrap signal, not a blanket yield ratio."""
    result = await session.execute(
        select(
            Build.product_id,
            Build.variant_id,
            BuildFailedConsumption.material_id,
            BuildFailedConsumption.qty_consumed,
        )
        .join(BuildFailedConsumption, BuildFailedConsumption.build_id == Build.id)
        .where(Build.built_at >= cutoff, BuildFailedConsumption.was_consumed.is_(True))
    )
    out: dict[tuple[int, int | None], dict[int, Decimal]] = defaultdict(dict)
    for product_id, variant_id, material_id, qty_consumed in result:
        entity_scrap = out[(product_id, variant_id)]
        entity_scrap[material_id] = entity_scrap.get(material_id, Decimal(0)) + Decimal(qty_consumed)
    return out


async def _get_on_order_lines(
    session: AsyncSession, default_lead_time_weeks: Decimal, today: date
) -> dict[int, list[tuple[Decimal, Decimal]]]:
    """Per material, every on-order purchase line as a (arrival_weeks_from_now, qty)
    timed inflow — using the PO's own expected_arrival_date when set, else estimating
    from order_date + the shop-wide default lead time."""
    result = await session.execute(
        select(MaterialPurchase.material_id, MaterialPurchase.qty, Purchase.order_date, Purchase.expected_arrival_date)
        .join(Purchase, Purchase.id == MaterialPurchase.purchase_id)
        .where(Purchase.status == PurchaseStatus.ordered)
    )
    out: dict[int, list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for material_id, qty, order_date, expected_arrival_date in result:
        arrival_date = expected_arrival_date or (order_date + timedelta(weeks=float(default_lead_time_weeks)))
        arrival_weeks = Decimal((arrival_date - today).days) / Decimal(7)
        if arrival_weeks < 0:
            arrival_weeks = Decimal(0)
        out[material_id].append((arrival_weeks, Decimal(qty)))
    return out


async def _resolve_build_bom(session: AsyncSession, product_id: int, variant_id: int | None) -> list[tuple[int, Decimal]]:
    if variant_id is not None:
        bom = await get_resolved_variant_bom(session, product_id, variant_id)
        return [(line.material_id, line.qty_required) for line in bom]
    result = await session.execute(
        select(ProductMaterial.material_id, ProductMaterial.qty_required).where(ProductMaterial.product_id == product_id)
    )
    return [(row.material_id, Decimal(row.qty_required)) for row in result]


async def _resolve_kitting_bom(session: AsyncSession, product_id: int, variant_id: int | None) -> list[tuple[int, Decimal]]:
    bom = await get_resolved_kitting_bom(session, product_id, variant_id)
    return [(line.material_id, line.qty_required) for line in bom]


def _piecewise_weeks_of_supply(
    position: Decimal, pieces: list[_DemandPiece], inflows: list[tuple[Decimal, Decimal]]
) -> Decimal | None:
    """Weeks until `position` runs out, given demand pieces that each start drawing at
    their own buffer_weeks and inflows that add stock at their own arrival week. Returns
    None if there's no active net demand by the time every breakpoint has passed."""
    breakpoints = sorted({p.buffer_weeks for p in pieces} | {arrival for arrival, _ in inflows} | {Decimal(0)})
    t_prev = Decimal(0)
    remaining = position
    for t in breakpoints:
        if t > t_prev:
            rate = sum((p.rate for p in pieces if p.buffer_weeks <= t_prev), Decimal(0))
            span = t - t_prev
            draw = rate * span
            if rate > 0 and draw >= remaining:
                return t_prev + (remaining / rate)
            remaining -= draw
            t_prev = t
        for arrival, qty in inflows:
            if arrival == t:
                remaining += qty

    final_rate = sum((p.rate for p in pieces if p.buffer_weeks <= t_prev), Decimal(0))
    if final_rate <= 0:
        return None
    return t_prev + (remaining / final_rate)


async def compute_material_forecasts(session: AsyncSession) -> list[MaterialForecast]:
    settings = await get_general_settings(session)
    lookback_weeks = Decimal(settings.forecast_lookback_weeks)
    default_lead_time_weeks = Decimal(str(settings.default_lead_time_weeks))
    warning_weeks = Decimal(str(settings.forecast_warning_weeks))
    critical_weeks = Decimal(str(settings.forecast_critical_weeks))

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(weeks=float(lookback_weeks))

    entities = await _get_sellable_entities(session)
    sales = await _get_sales_by_entity(session, cutoff, now)
    builds = await _get_builds_by_entity(session, cutoff)
    scrap = await _get_scrap_by_entity_material(session, cutoff)
    materials = list(await session.execute(_MATERIALS_SQL))
    on_order = await _get_on_order_lines(session, default_lead_time_weeks, now.date())

    demand_pieces: dict[int, list[_DemandPiece]] = defaultdict(list)
    active_weeks: dict[int, set[int]] = defaultdict(set)

    for e in entities:
        key = (e.product_id, e.variant_id)
        total_qty, weeks_with_sales = sales.get(key, (Decimal(0), set()))
        sales_rate = total_qty / lookback_weeks if lookback_weeks > 0 else Decimal(0)
        if sales_rate <= 0:
            continue

        qty_built, qty_failed = builds.get(key, (Decimal(0), Decimal(0)))
        failure_rate = (qty_failed / qty_built) if qty_built > 0 else Decimal(0)
        entity_scrap = scrap.get(key, {})

        fg_buffer = (e.current_stock - e.allocated_qty) / sales_rate
        if fg_buffer < 0:
            fg_buffer = Decimal(0)

        for material_id, qty_required in await _resolve_build_bom(session, e.product_id, e.variant_id):
            scrap_per_failed_unit = (
                entity_scrap.get(material_id, Decimal(0)) / qty_failed if qty_failed > 0 else Decimal(0)
            )
            rate = sales_rate * qty_required + sales_rate * failure_rate * scrap_per_failed_unit
            if rate > 0:
                demand_pieces[material_id].append(_DemandPiece(buffer_weeks=fg_buffer, rate=rate))
                active_weeks[material_id] |= weeks_with_sales

        for material_id, qty_required in await _resolve_kitting_bom(session, e.product_id, e.variant_id):
            rate = sales_rate * qty_required
            if rate > 0:
                demand_pieces[material_id].append(_DemandPiece(buffer_weeks=Decimal(0), rate=rate))
                active_weeks[material_id] |= weeks_with_sales

    forecasts: list[MaterialForecast] = []
    for m in materials:
        current_qty = Decimal(m.current_qty)
        allocated_qty = Decimal(m.allocated_qty)
        reorder_threshold = Decimal(m.reorder_threshold)
        inflows = on_order.get(m.id, [])
        on_order_qty = sum((qty for _, qty in inflows), Decimal(0))
        pieces = demand_pieces.get(m.id, [])
        sufficient_history = len(active_weeks.get(m.id, set())) >= _MIN_ACTIVE_WEEKS_FOR_FORECAST

        if not pieces or not sufficient_history:
            if reorder_threshold > 0 and current_qty <= reorder_threshold:
                forecasts.append(
                    MaterialForecast(
                        material_id=m.id,
                        name=m.name,
                        current_qty=current_qty,
                        allocated_qty=allocated_qty,
                        on_order_qty=on_order_qty,
                        reorder_threshold=reorder_threshold,
                        supplier_id=m.default_supplier_id,
                        supplier_name=m.supplier_name,
                        consumption_rate_per_week=None,
                        weeks_of_supply=None,
                        fg_buffer_weeks=None,
                        status="insufficient_data",
                    )
                )
            continue

        position = current_qty - allocated_qty
        weeks = _piecewise_weeks_of_supply(position, pieces, inflows)
        weeks_no_fg = _piecewise_weeks_of_supply(
            position, [_DemandPiece(Decimal(0), p.rate) for p in pieces], inflows
        )
        fg_buffer_weeks = (
            (weeks - weeks_no_fg) if (weeks is not None and weeks_no_fg is not None) else Decimal(0)
        )
        consumption_rate_per_week = sum((p.rate for p in pieces), Decimal(0))

        if weeks is not None and weeks <= critical_weeks:
            status = "critical"
        elif weeks is not None and weeks <= warning_weeks:
            status = "warning"
        elif reorder_threshold > 0 and current_qty <= reorder_threshold:
            status = "warning"  # manual floor override — forecast says fine, but the user's own floor says otherwise
        else:
            continue  # ok — nothing to surface

        forecasts.append(
            MaterialForecast(
                material_id=m.id,
                name=m.name,
                current_qty=current_qty,
                allocated_qty=allocated_qty,
                on_order_qty=on_order_qty,
                reorder_threshold=reorder_threshold,
                supplier_id=m.default_supplier_id,
                supplier_name=m.supplier_name,
                consumption_rate_per_week=consumption_rate_per_week,
                weeks_of_supply=weeks,
                fg_buffer_weeks=fg_buffer_weeks,
                status=status,
            )
        )

    forecasts.sort(
        key=lambda f: (
            f.supplier_id is None,
            f.supplier_name or "",
            f.weeks_of_supply is None,
            f.weeks_of_supply if f.weeks_of_supply is not None else Decimal(0),
        )
    )
    return forecasts
