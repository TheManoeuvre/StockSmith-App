from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.abc import DueForCountItemRead
from app.schemas.dashboard import (
    BuildableProduct,
    DashboardSummary,
    LowStockMaterial,
    MarginAlert,
    OpenStockTake,
    OrderAwaitingInventory,
)
from app.schemas.variant import VariantBomLine
from app.services.abc import compute_due_for_count
from app.services.platforms.base import ensure_utc
from app.services.purchase_sql import ON_ORDER_BY_MATERIAL_SQL

_ORDERS_AWAITING_INVENTORY_SQL = text(
    """
    SELECT ol.id AS line_id, ol.order_id, ol.product_id, ol.variant_id,
           p.name AS product_name, pv.variant_name AS variant_name,
           (ol.ordered_qty - ol.allocated_qty) AS short_by,
           o.order_placed_at
    FROM order_lines ol
    JOIN orders o ON o.id = ol.order_id
    LEFT JOIN products p ON p.id = ol.product_id
    LEFT JOIN product_variants pv ON pv.id = ol.variant_id
    WHERE ol.allocated_qty < ol.ordered_qty
      AND ol.needs_mapping = false
      AND o.status NOT IN ('cancelled', 'shipped')
    ORDER BY (ol.ordered_qty - ol.allocated_qty) DESC, o.order_placed_at ASC
    """
)

_MAX_BUILDABLE_BY_PRODUCT_SQL = text(
    """
    SELECT pm.product_id, MIN(FLOOR(m.current_qty / pm.qty_required)) AS max_buildable
    FROM product_materials pm
    JOIN materials m ON m.id = pm.material_id
    GROUP BY pm.product_id
    """
)

_EXPECTED_MAX_BUILDABLE_BY_PRODUCT_SQL = text(
    f"""
    SELECT pm.product_id,
           MIN(FLOOR((m.current_qty + COALESCE(oo.on_order_qty, 0)) / pm.qty_required)) AS expected_max_buildable
    FROM product_materials pm
    JOIN materials m ON m.id = pm.material_id
    LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) oo ON oo.material_id = m.id
    GROUP BY pm.product_id
    """
)

_COST_PER_UNIT_BY_PRODUCT_SQL = text(
    """
    SELECT pm.product_id, SUM(pm.qty_required * m.avg_unit_cost) AS cost_per_unit
    FROM product_materials pm
    JOIN materials m ON m.id = pm.material_id
    GROUP BY pm.product_id
    """
)

_ACTIVE_VARIANT_STOCK_TOTALS_BY_PRODUCT_SQL = text(
    """
    SELECT product_id, SUM(current_stock) AS total_current_stock, SUM(allocated_qty) AS total_allocated_qty
    FROM product_variants
    WHERE is_active = true
    GROUP BY product_id
    """
)

_RESOLVED_VARIANT_BOM_SQL = text(
    """
    SELECT pm.material_id, COALESCE(pvm.qty_required, pm.qty_required) AS effective_qty_required,
           CAST(NULL AS INTEGER) AS replaces_material_id
    FROM product_materials pm
    LEFT JOIN product_variant_materials pvm
        ON pvm.variant_id = :variant_id AND pvm.material_id = pm.material_id AND pvm.replaces_material_id IS NULL
    WHERE pm.product_id = :product_id
      AND pm.material_id NOT IN (
          SELECT replaces_material_id FROM product_variant_materials
          WHERE variant_id = :variant_id AND replaces_material_id IS NOT NULL
      )

    UNION ALL

    SELECT pvm.material_id, pvm.qty_required AS effective_qty_required, pvm.replaces_material_id
    FROM product_variant_materials pvm
    WHERE pvm.variant_id = :variant_id
      AND (
          pvm.replaces_material_id IS NOT NULL
          OR pvm.material_id NOT IN (SELECT material_id FROM product_materials WHERE product_id = :product_id)
      )
    """
)

_RESOLVED_PRODUCT_VARIANTS_BOM_SQL = text(
    """
    SELECT v.id AS variant_id, pm.material_id,
           COALESCE(qo.qty_required, pm.qty_required) AS effective_qty_required,
           CAST(NULL AS INTEGER) AS replaces_material_id
    FROM product_variants v
    CROSS JOIN product_materials pm
    LEFT JOIN product_variant_materials qo
        ON qo.variant_id = v.id AND qo.material_id = pm.material_id AND qo.replaces_material_id IS NULL
    LEFT JOIN product_variant_materials sub
        ON sub.variant_id = v.id AND sub.replaces_material_id = pm.material_id
    WHERE v.product_id = :product_id AND pm.product_id = :product_id AND sub.id IS NULL

    UNION ALL

    SELECT pvm.variant_id, pvm.material_id, pvm.qty_required, pvm.replaces_material_id
    FROM product_variant_materials pvm
    JOIN product_variants v ON v.id = pvm.variant_id
    WHERE v.product_id = :product_id
      AND (
          pvm.replaces_material_id IS NOT NULL
          OR pvm.material_id NOT IN (SELECT material_id FROM product_materials WHERE product_id = :product_id)
      )
    """
)


# The catalogue-wide analog of _RESOLVED_PRODUCT_VARIANTS_BOM_SQL, aggregated straight to a
# per-product cost range. Same two-arm resolution (base BOM minus anything substituted away,
# then the variant's own overrides and additions), with every `product_id = :product_id`
# filter replaced by a correlation on v.product_id so one query covers every product.
#
# Exists because get_cost_per_unit_by_product is base-BOM only: for a product whose variants
# override their BOM, the single figure it returns can match no actual variant. The products
# list showed that figure as "Cost per unit" — not merely incomplete, potentially wrong.
#
# Only active variants count. A disabled variant is not sellable, so letting it widen the
# range would misreport what the product actually costs to fulfil today — same reasoning as
# _ACTIVE_VARIANT_STOCK_TOTALS_BY_PRODUCT_SQL above.
#
# A variant whose resolved BOM is empty contributes no row and so is simply absent from the
# MIN/MAX, mirroring how get_cost_per_unit_by_product omits a product with no BOM rather than
# reporting it as free. A product where no variant has a BOM therefore doesn't appear at all.
_VARIANT_COST_RANGE_BY_PRODUCT_SQL = text(
    """
    WITH resolved AS (
        SELECT v.product_id, v.id AS variant_id, pm.material_id,
               COALESCE(qo.qty_required, pm.qty_required) AS effective_qty_required
        FROM product_variants v
        JOIN product_materials pm ON pm.product_id = v.product_id
        LEFT JOIN product_variant_materials qo
            ON qo.variant_id = v.id AND qo.material_id = pm.material_id AND qo.replaces_material_id IS NULL
        LEFT JOIN product_variant_materials sub
            ON sub.variant_id = v.id AND sub.replaces_material_id = pm.material_id
        WHERE v.is_active = true AND sub.id IS NULL

        UNION ALL

        SELECT v.product_id, pvm.variant_id, pvm.material_id, pvm.qty_required
        FROM product_variant_materials pvm
        JOIN product_variants v ON v.id = pvm.variant_id
        WHERE v.is_active = true
          AND (
              pvm.replaces_material_id IS NOT NULL
              OR pvm.material_id NOT IN (
                  SELECT material_id FROM product_materials WHERE product_id = v.product_id
              )
          )
    ),
    variant_cost AS (
        SELECT r.product_id, r.variant_id,
               SUM(r.effective_qty_required * m.avg_unit_cost) AS cost_per_unit
        FROM resolved r
        JOIN materials m ON m.id = r.material_id
        WHERE r.effective_qty_required > 0
        GROUP BY r.product_id, r.variant_id
    )
    SELECT product_id, MIN(cost_per_unit) AS cost_min, MAX(cost_per_unit) AS cost_max
    FROM variant_cost
    GROUP BY product_id
    """
)


_READY_TO_SHIP_BY_BUNDLE_SQL = text(
    """
    SELECT pbi.bundle_product_id, MIN(p.current_stock / pbi.qty) AS ready_to_ship
    FROM product_bundle_items pbi
    JOIN products p ON p.id = pbi.component_product_id
    GROUP BY pbi.bundle_product_id
    """
)


async def get_max_buildable_by_product(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(_MAX_BUILDABLE_BY_PRODUCT_SQL)
    return {row.product_id: int(row.max_buildable) for row in result}


async def get_expected_max_buildable_by_product(session: AsyncSession) -> dict[int, int]:
    """Like get_max_buildable_by_product, but also counts materials already on an open
    purchase order — i.e. what could be built once pending orders arrive, not just what
    could be built right now."""
    result = await session.execute(_EXPECTED_MAX_BUILDABLE_BY_PRODUCT_SQL)
    return {row.product_id: int(row.expected_max_buildable) for row in result}


async def get_active_variant_stock_totals_by_product(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """A product with active variants never accumulates its own current_stock/
    allocated_qty — builds always target the variant row instead (see builds.py) — so
    the bare Product row's counters stay at 0 even when its variants collectively hold
    real stock. Sums (current_stock, allocated_qty) across just the active variants so
    product-level views (products list, single GET) can show the real total instead of
    the always-zero bare-product counters. A product absent from this dict has no active
    variants; callers fall back to the product's own current_stock/allocated_qty."""
    result = await session.execute(_ACTIVE_VARIANT_STOCK_TOTALS_BY_PRODUCT_SQL)
    return {row.product_id: (int(row.total_current_stock), int(row.total_allocated_qty)) for row in result}


async def get_ready_to_ship_by_bundle(session: AsyncSession) -> dict[int, int]:
    """Bundle buildability is based on components' current_stock (actual on-hand
    finished goods already built), not their theoretical max_buildable — a bundle
    ships components you've already built, not ones you could build."""
    result = await session.execute(_READY_TO_SHIP_BY_BUNDLE_SQL)
    return {row.bundle_product_id: int(row.ready_to_ship) for row in result}


async def get_bundle_cost_per_unit(
    session: AsyncSession, cost_per_unit_by_product: dict[int, Decimal]
) -> dict[int, Decimal]:
    """A bundle's cost is the sum of its components' cost_per_unit * qty. Components
    can't themselves be bundles (enforced at the application layer), so this needs no
    recursion."""
    result = await session.execute(text("SELECT bundle_product_id, component_product_id, qty FROM product_bundle_items"))
    totals: dict[int, Decimal] = {}
    for row in result:
        component_cost = cost_per_unit_by_product.get(row.component_product_id)
        if component_cost is None:
            continue
        totals[row.bundle_product_id] = totals.get(row.bundle_product_id, Decimal(0)) + component_cost * row.qty
    return totals


async def get_cost_per_unit_by_product(session: AsyncSession) -> dict[int, Decimal]:
    result = await session.execute(_COST_PER_UNIT_BY_PRODUCT_SQL)
    return {row.product_id: Decimal(row.cost_per_unit) for row in result}


async def get_cost_per_unit_range_by_product(session: AsyncSession) -> dict[int, tuple[Decimal, Decimal]]:
    """(lowest, highest) build cost across a product's ACTIVE variants, for the whole
    catalogue in one query — the variant-aware companion to get_cost_per_unit_by_product,
    which resolves the base BOM only. Products with no variants (or none with a BOM) are
    absent, exactly as they are there.

    str() before Decimal: SQLite returns these SUMs as floats, and Decimal(float) expands
    the full binary representation into something unreadable. Same treatment as
    kitting.get_kitting_cogs_by_order, for the same reason."""
    result = await session.execute(_VARIANT_COST_RANGE_BY_PRODUCT_SQL)
    return {
        row.product_id: (Decimal(str(row.cost_min)), Decimal(str(row.cost_max)))
        for row in result
        if row.cost_min is not None
    }


async def get_resolved_variant_bom(session: AsyncSession, product_id: int, variant_id: int) -> list[VariantBomLine]:
    result = await session.execute(
        _RESOLVED_VARIANT_BOM_SQL, {"product_id": product_id, "variant_id": variant_id}
    )
    return [
        VariantBomLine(
            material_id=row.material_id,
            qty_required=Decimal(row.effective_qty_required),
            replaces_material_id=row.replaces_material_id,
        )
        for row in result
        if row.effective_qty_required > 0
    ]


async def compute_variant_buildability(
    session: AsyncSession, product_id: int, variant_id: int
) -> tuple[int | None, int | None, Decimal | None, list[VariantBomLine]]:
    bom = await get_resolved_variant_bom(session, product_id, variant_id)
    if not bom:
        return None, None, None, bom

    material_ids = [line.material_id for line in bom]
    rows = await session.execute(
        text(
            f"""
            SELECT m.id, m.current_qty, m.avg_unit_cost, COALESCE(oo.on_order_qty, 0) AS on_order_qty
            FROM materials m
            LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) oo ON oo.material_id = m.id
            WHERE m.id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": material_ids},
    )
    materials = {row.id: row for row in rows}

    # Attach each line's own bottleneck (how many units *this material alone* would
    # allow) directly on the returned line objects — the overall max_buildable/
    # expected_max_buildable are just the min() of these per-line values, so computing
    # them here rather than discarding them lets the BOM editor show which material is
    # the actual constraint.
    for line in bom:
        m = materials[line.material_id]
        line.line_max_buildable = int(Decimal(m.current_qty) // line.qty_required)
        line.line_expected_max_buildable = int((Decimal(m.current_qty) + Decimal(m.on_order_qty)) // line.qty_required)

    max_buildable = min(line.line_max_buildable for line in bom)
    expected_max_buildable = min(line.line_expected_max_buildable for line in bom)
    cost_per_unit = sum(
        (Decimal(materials[line.material_id].avg_unit_cost) * line.qty_required for line in bom),
        start=Decimal(0),
    )
    return max_buildable, expected_max_buildable, cost_per_unit, bom


async def get_resolved_variant_boms_by_variant(
    session: AsyncSession, product_id: int
) -> dict[int, list[VariantBomLine]]:
    """Bulk analog of get_resolved_variant_bom — resolves every variant's effective BOM
    in one query instead of one query per variant. A variant with no BOM at all is
    simply absent from the returned dict (matching get_resolved_variant_bom's empty-list
    return for that case) — callers must use .get(variant_id, []), never a bare index."""
    result = await session.execute(_RESOLVED_PRODUCT_VARIANTS_BOM_SQL, {"product_id": product_id})
    boms: dict[int, list[VariantBomLine]] = {}
    for row in result:
        if row.effective_qty_required <= 0:
            continue
        boms.setdefault(row.variant_id, []).append(
            VariantBomLine(
                material_id=row.material_id,
                qty_required=Decimal(row.effective_qty_required),
                replaces_material_id=row.replaces_material_id,
            )
        )
    return boms


async def compute_variants_buildability_bulk(
    session: AsyncSession, product_id: int, variant_ids: list[int]
) -> dict[int, tuple[int | None, int | None, Decimal | None, list[VariantBomLine]]]:
    """Bulk analog of compute_variant_buildability for every variant_id given (all must
    belong to product_id). 2 queries total regardless of len(variant_ids), instead of
    2 queries per variant."""
    boms_by_variant = await get_resolved_variant_boms_by_variant(session, product_id)

    all_material_ids = {line.material_id for bom in boms_by_variant.values() for line in bom}
    materials: dict[int, object] = {}
    if all_material_ids:
        rows = await session.execute(
            text(
                f"""
                SELECT m.id, m.current_qty, m.avg_unit_cost, COALESCE(oo.on_order_qty, 0) AS on_order_qty
                FROM materials m
                LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) oo ON oo.material_id = m.id
                WHERE m.id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": list(all_material_ids)},
        )
        materials = {row.id: row for row in rows}

    results: dict[int, tuple[int | None, int | None, Decimal | None, list[VariantBomLine]]] = {}
    for variant_id in variant_ids:
        bom = boms_by_variant.get(variant_id, [])
        if not bom:
            results[variant_id] = (None, None, None, bom)
            continue
        for line in bom:
            m = materials[line.material_id]
            line.line_max_buildable = int(Decimal(m.current_qty) // line.qty_required)
            line.line_expected_max_buildable = int(
                (Decimal(m.current_qty) + Decimal(m.on_order_qty)) // line.qty_required
            )
        max_buildable = min(line.line_max_buildable for line in bom)
        expected_max_buildable = min(line.line_expected_max_buildable for line in bom)
        cost_per_unit = sum(
            (Decimal(materials[line.material_id].avg_unit_cost) * line.qty_required for line in bom),
            start=Decimal(0),
        )
        results[variant_id] = (max_buildable, expected_max_buildable, cost_per_unit, bom)
    return results


async def get_orders_awaiting_inventory(session: AsyncSession) -> list[OrderAwaitingInventory]:
    """Open order lines that can't be fully allocated from current free stock, most-shorted
    first — surfaced on the dashboard with a "Build now" action per the allocation feature."""
    result = await session.execute(_ORDERS_AWAITING_INVENTORY_SQL)
    return [
        OrderAwaitingInventory(
            line_id=row.line_id,
            order_id=row.order_id,
            product_id=row.product_id,
            variant_id=row.variant_id,
            product_name=row.product_name,
            variant_name=row.variant_name,
            short_by=int(row.short_by),
            order_placed_at=row.order_placed_at,
        )
        for row in result
    ]


async def compute_dashboard_summary(session: AsyncSession) -> DashboardSummary:
    inventory_value_row = (
        await session.execute(
            text("SELECT COALESCE(SUM(current_qty * avg_unit_cost), 0) AS total FROM materials WHERE is_active = true")
        )
    ).one()
    active_product_count = (
        await session.execute(text("SELECT COUNT(*) AS n FROM products WHERE is_active = true"))
    ).one().n

    from app.services.forecasting import compute_material_forecasts

    forecasts = await compute_material_forecasts(session)
    low_stock = [
        LowStockMaterial(
            id=f.material_id,
            name=f.name,
            current_qty=f.current_qty,
            reorder_threshold=f.reorder_threshold,
            on_order_qty=f.on_order_qty,
            allocated_qty=f.allocated_qty,
            supplier_id=f.supplier_id,
            supplier_name=f.supplier_name,
            consumption_rate_per_week=f.consumption_rate_per_week,
            weeks_of_supply=f.weeks_of_supply,
            fg_buffer_weeks=f.fg_buffer_weeks,
            lead_time_weeks=f.lead_time_weeks,
            status=f.status,
        )
        for f in forecasts
    ]

    max_buildable_by_product = await get_max_buildable_by_product(session)
    expected_max_buildable_by_product = await get_expected_max_buildable_by_product(session)
    product_rows = await session.execute(text("SELECT id, name FROM products WHERE is_active = true"))
    buildable = [
        BuildableProduct(
            product_id=row.id,
            name=row.name,
            max_buildable=max_buildable_by_product.get(row.id),
            expected_max_buildable=expected_max_buildable_by_product.get(row.id),
        )
        for row in product_rows
    ]
    buildable.sort(key=lambda p: (p.max_buildable is None, p.max_buildable))

    from app.services.pricing import get_margin_alerts
    from app.services.kitting import get_orders_awaiting_packaging

    margin_alerts = [MarginAlert(**alert) for alert in await get_margin_alerts(session)]
    orders_awaiting_inventory = await get_orders_awaiting_inventory(session)
    orders_awaiting_packaging = await get_orders_awaiting_packaging(session)

    # Capped like lowest_buildable_products. On a database that has never had a stock take
    # this is the entire catalogue, and the dashboard wants the worst offenders, not a
    # thousand-row dump — items_due_for_count_total carries the real size so the section
    # can say "showing 10 of 412" rather than implying there are only ten.
    items_due_for_count = await compute_due_for_count(session)

    from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus

    unresolved_variance_count = (
        await session.execute(
            select(func.count())
            .select_from(StockTakeLine)
            .join(StockTake, StockTakeLine.stock_take_id == StockTake.id)
            .where(
                StockTakeLine.status == StockTakeLineStatus.conflict,
                StockTake.status == StockTakeStatus.closed,
            )
        )
    ).scalar_one()

    # Only one take is normally open; if somehow more are, the oldest is the one whose
    # accumulated movement is the problem worth surfacing.
    open_take_row = (
        await session.execute(
            select(StockTake).where(StockTake.status == StockTakeStatus.open).order_by(StockTake.started_at).limit(1)
        )
    ).scalar_one_or_none()
    open_stock_take = None
    if open_take_row is not None:
        take_lines = list(
            (
                await session.execute(
                    select(StockTakeLine).where(StockTakeLine.stock_take_id == open_take_row.id)
                )
            ).scalars()
        )
        started = ensure_utc(open_take_row.started_at) or datetime.now(timezone.utc)
        open_stock_take = OpenStockTake(
            id=open_take_row.id,
            started_at=started,
            open_days=max((datetime.now(timezone.utc) - started).days, 0),
            line_count=len(take_lines),
            counted_count=sum(1 for line in take_lines if line.status is StockTakeLineStatus.counted),
        )

    return DashboardSummary(
        total_inventory_value=Decimal(inventory_value_row.total),
        active_product_count=active_product_count,
        low_stock_materials=low_stock,
        lowest_buildable_products=buildable[:10],
        margin_alerts=margin_alerts,
        orders_awaiting_inventory=orders_awaiting_inventory,
        orders_awaiting_packaging=orders_awaiting_packaging,
        items_due_for_count=[DueForCountItemRead.model_validate(item) for item in items_due_for_count[:10]],
        items_due_for_count_total=len(items_due_for_count),
        unresolved_variance_count=unresolved_variance_count,
        open_stock_take=open_stock_take,
    )
