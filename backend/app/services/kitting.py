from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kitting import OrderKittingAllocation, OrderKittingOverride, ProductKittingMaterial
from app.models.listing import Listing, ListingPlatform
from app.models.material import Material, MaterialAdjustment
from app.models.order import Order, OrderLine, OrderStatus
from app.models.variant import ProductVariant
from app.schemas.dashboard import OrderAwaitingPackaging
from app.schemas.kitting import OrderKittingOverrideLine, OrderKittingRequirementLine, OrderKittingSummary, VariantKittingBomLine
from app.services.costing import recompute_material

# Mirrors costing.py's/buildability.py's own copy of this subquery — kept as a
# standalone fragment here too rather than importing across modules, per this codebase's
# existing convention for these small self-contained SQL fragments.
_ON_ORDER_BY_MATERIAL_SUBQUERY = """
    SELECT mp.material_id, SUM(mp.qty) AS on_order_qty
    FROM material_purchases mp
    JOIN purchases p ON p.id = mp.purchase_id
    WHERE p.status = 'ordered'
    GROUP BY mp.material_id
"""

_KITTING_CAPACITY_BY_PRODUCT_SQL = text(
    """
    SELECT pkm.product_id, MIN(FLOOR((m.current_qty - m.allocated_qty) / pkm.qty_required)) AS kitting_capacity
    FROM product_kitting_materials pkm
    JOIN materials m ON m.id = pkm.material_id
    GROUP BY pkm.product_id
    """
)

_EXPECTED_KITTING_CAPACITY_BY_PRODUCT_SQL = text(
    f"""
    SELECT pkm.product_id,
           MIN(FLOOR((m.current_qty - m.allocated_qty + COALESCE(oo.on_order_qty, 0)) / pkm.qty_required))
               AS expected_kitting_capacity
    FROM product_kitting_materials pkm
    JOIN materials m ON m.id = pkm.material_id
    LEFT JOIN ({_ON_ORDER_BY_MATERIAL_SUBQUERY}) oo ON oo.material_id = m.id
    GROUP BY pkm.product_id
    """
)

_RESOLVED_VARIANT_KITTING_BOM_SQL = text(
    """
    SELECT pkm.material_id, COALESCE(pvkm.qty_required, pkm.qty_required) AS effective_qty_required,
           CAST(NULL AS INTEGER) AS replaces_material_id
    FROM product_kitting_materials pkm
    LEFT JOIN product_variant_kitting_materials pvkm
        ON pvkm.variant_id = :variant_id AND pvkm.material_id = pkm.material_id AND pvkm.replaces_material_id IS NULL
    WHERE pkm.product_id = :product_id
      AND pkm.material_id NOT IN (
          SELECT replaces_material_id FROM product_variant_kitting_materials
          WHERE variant_id = :variant_id AND replaces_material_id IS NOT NULL
      )

    UNION ALL

    SELECT pvkm.material_id, pvkm.qty_required AS effective_qty_required, pvkm.replaces_material_id
    FROM product_variant_kitting_materials pvkm
    WHERE pvkm.variant_id = :variant_id
      AND (
          pvkm.replaces_material_id IS NOT NULL
          OR pvkm.material_id NOT IN (SELECT material_id FROM product_kitting_materials WHERE product_id = :product_id)
      )
    """
)


_RESOLVED_PRODUCT_VARIANTS_KITTING_BOM_SQL = text(
    """
    SELECT v.id AS variant_id, pkm.material_id,
           COALESCE(qo.qty_required, pkm.qty_required) AS effective_qty_required,
           CAST(NULL AS INTEGER) AS replaces_material_id
    FROM product_variants v
    CROSS JOIN product_kitting_materials pkm
    LEFT JOIN product_variant_kitting_materials qo
        ON qo.variant_id = v.id AND qo.material_id = pkm.material_id AND qo.replaces_material_id IS NULL
    LEFT JOIN product_variant_kitting_materials sub
        ON sub.variant_id = v.id AND sub.replaces_material_id = pkm.material_id
    WHERE v.product_id = :product_id AND pkm.product_id = :product_id AND sub.id IS NULL

    UNION ALL

    SELECT pvkm.variant_id, pvkm.material_id, pvkm.qty_required, pvkm.replaces_material_id
    FROM product_variant_kitting_materials pvkm
    JOIN product_variants v ON v.id = pvkm.variant_id
    WHERE v.product_id = :product_id
      AND (
          pvkm.replaces_material_id IS NOT NULL
          OR pvkm.material_id NOT IN (SELECT material_id FROM product_kitting_materials WHERE product_id = :product_id)
      )
    """
)


async def get_resolved_kitting_bom(
    session: AsyncSession, product_id: int, variant_id: int | None
) -> list[VariantKittingBomLine]:
    """Resolves the effective kitting BOM for a product or one of its variants — same
    base+override resolution shape as buildability.get_resolved_variant_bom, but against
    the kitting tables, and (unlike that function) also handles variant_id=None directly
    so every caller (reconciliation, buildability, CRUD "effective view") can share one
    entry point instead of each branching on "no variant" themselves like builds.py does
    for the build BOM."""
    if variant_id is None:
        result = await session.execute(
            select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product_id)
        )
        return [
            VariantKittingBomLine(material_id=row.material_id, qty_required=Decimal(row.qty_required))
            for row in result.scalars()
        ]

    result = await session.execute(
        _RESOLVED_VARIANT_KITTING_BOM_SQL, {"product_id": product_id, "variant_id": variant_id}
    )
    return [
        VariantKittingBomLine(
            material_id=row.material_id,
            qty_required=Decimal(row.effective_qty_required),
            replaces_material_id=row.replaces_material_id,
        )
        for row in result
        if row.effective_qty_required > 0
    ]


async def get_kitting_capacity_by_product(session: AsyncSession) -> dict[int, int]:
    """Bulk (base-product-only, no variant resolution) free kitting capacity per product —
    the packaging analog of buildability.get_max_buildable_by_product. Products with no
    kitting BOM at all are simply absent from the result; the caller treats that as "no
    packaging constraint" rather than zero."""
    result = await session.execute(_KITTING_CAPACITY_BY_PRODUCT_SQL)
    return {row.product_id: int(row.kitting_capacity) for row in result}


async def get_expected_kitting_capacity_by_product(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(_EXPECTED_KITTING_CAPACITY_BY_PRODUCT_SQL)
    return {row.product_id: int(row.expected_kitting_capacity) for row in result}


def combine_max_sellable(free_stock: int, kitting_capacity: int | None) -> tuple[int, str | None]:
    """free_stock is current_stock - allocated_qty; kitting_capacity is None when the
    product/variant has no kitting BOM at all, meaning packaging isn't a constraint.

    Returns (max_sellable, reason) — reason is "stock" or "packaging", whichever side of
    the min() actually won, so callers can explain why the number is what it is. Reason
    is None only when there's no kitting BOM at all: with nothing to compare free_stock
    against, there's no real constraint to name (max_sellable is just free_stock,
    unflagged, same as before this function reported a reason at all)."""
    if kitting_capacity is None:
        return free_stock, None
    if free_stock <= kitting_capacity:
        return free_stock, "stock"
    return kitting_capacity, "packaging"


def combine_expected_max_sellable(
    expected_max_buildable: int | None, expected_kitting_capacity: int | None
) -> tuple[int | None, str | None]:
    """Same shape as combine_max_sellable, but reason is "materials" or "packaging".
    If a kitting BOM exists but the product has no build BOM at all
    (expected_max_buildable is None), packaging is the only real number in play, so it's
    reported as the reason rather than None — unlike the no-kitting-BOM case, there IS a
    genuine constraint here, just nothing to compare it against."""
    if expected_kitting_capacity is None:
        return expected_max_buildable, None
    if expected_max_buildable is None:
        return expected_kitting_capacity, "packaging"
    if expected_max_buildable <= expected_kitting_capacity:
        return expected_max_buildable, "materials"
    return expected_kitting_capacity, "packaging"


def combine_theoretical_max_sellable(
    free_stock: int, max_buildable: int | None, kitting_capacity: int | None
) -> tuple[int, str | None]:
    """What could be sold right now if StockSmith builds to backfill an order rather than
    only selling already-assembled stock: free_stock (already built, unreserved) PLUS
    max_buildable (more buildable from raw materials already on hand — NOT counting
    on-order materials, unlike expected_max_buildable/combine_expected_max_sellable,
    since the premise here is "build from what's actually in the building right now",
    not "eventually, once a purchase order lands"). Still capped by on-hand packaging
    capacity (kitting_capacity, not expected_kitting_capacity, for the same reason) —
    reason is "stock"/"materials" vs "packaging", mirroring combine_max_sellable's/
    combine_expected_max_sellable's vocabulary. Only used when a product's
    push_buildable_capacity flag is on (see listing_push._resolve_max_sellable); when
    it's off, combine_max_sellable's real, already-built-only figure is used instead."""
    capacity = free_stock + (max_buildable or 0)
    if kitting_capacity is None:
        return capacity, None
    if capacity <= kitting_capacity:
        return capacity, "materials"
    return kitting_capacity, "packaging"


async def compute_variant_kitting_capacity(
    session: AsyncSession, product_id: int, variant_id: int | None
) -> tuple[int | None, int | None, list[VariantKittingBomLine]]:
    """Per-variant (or bare-product, when variant_id is None) analog of
    buildability.compute_variant_buildability, but for packaging: how many MORE units
    could be packed right now from free on-hand packaging (kitting_capacity) vs
    eventually, counting on-order packaging too (expected_kitting_capacity). Returns
    (None, None, []) when there's no kitting BOM at all — packaging isn't tracked for
    this product/variant, so it should never be treated as a zero-capacity constraint."""
    bom = await get_resolved_kitting_bom(session, product_id, variant_id)
    if not bom:
        return None, None, bom

    material_ids = [line.material_id for line in bom]
    rows = await session.execute(
        text(
            f"""
            SELECT m.id, m.current_qty, m.allocated_qty, COALESCE(oo.on_order_qty, 0) AS on_order_qty
            FROM materials m
            LEFT JOIN ({_ON_ORDER_BY_MATERIAL_SUBQUERY}) oo ON oo.material_id = m.id
            WHERE m.id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": material_ids},
    )
    materials = {row.id: row for row in rows}

    for line in bom:
        m = materials[line.material_id]
        free = Decimal(m.current_qty) - Decimal(m.allocated_qty)
        expected_free = free + Decimal(m.on_order_qty)
        line.line_max_buildable = int(free // line.qty_required)
        line.line_expected_max_buildable = int(expected_free // line.qty_required)

    kitting_capacity = min(line.line_max_buildable for line in bom)
    expected_kitting_capacity = min(line.line_expected_max_buildable for line in bom)
    return kitting_capacity, expected_kitting_capacity, bom


async def compute_variant_kitting_cost_per_unit(
    session: AsyncSession, product_id: int, variant_id: int | None
) -> Decimal | None:
    """Packaging-cost analog of buildability.compute_variant_buildability's cost calc —
    SUM(qty_required * avg_unit_cost) over the resolved kitting BOM. Reuses
    get_resolved_kitting_bom, which already handles variant_id=None for a bare product,
    so unlike the build-BOM equivalent this needs no separate bare-product code path.
    Returns None when there's no kitting BOM at all (nothing to cost)."""
    bom = await get_resolved_kitting_bom(session, product_id, variant_id)
    if not bom:
        return None

    material_ids = [line.material_id for line in bom]
    rows = await session.execute(
        text("SELECT id, avg_unit_cost FROM materials WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": material_ids},
    )
    cost_by_id = {row.id: Decimal(row.avg_unit_cost) for row in rows}
    return sum((cost_by_id[line.material_id] * line.qty_required for line in bom), start=Decimal(0))


def _clamp_value_to_ceiling(
    value: int | None, reason: str | None, platform_ceiling_qty: int | None
) -> tuple[int | None, str | None]:
    if value is not None and platform_ceiling_qty is not None and platform_ceiling_qty < value:
        return platform_ceiling_qty, "ceiling"
    return value, reason


def apply_platform_ceiling(
    max_sellable: int,
    max_sellable_reason: str | None,
    expected_max_sellable: int | None,
    expected_max_sellable_reason: str | None,
    platform_ceiling_qty: int | None,
) -> tuple[int, str | None, int | None, str | None]:
    """Final clamp on top of the stock/packaging combination: a manual, product-level
    cap (Product.platform_ceiling_qty) applied uniformly to every variant's own numbers,
    with its own "ceiling" reason — but only when it's actually the tightest constraint
    (a variant already below the cap is untouched). Shared by compute_max_sellable (the
    per-variant path) and _read_product's bulk dict-based path, which combines the same
    way but without a kitting BOM to resolve."""
    max_sellable, max_sellable_reason = _clamp_value_to_ceiling(max_sellable, max_sellable_reason, platform_ceiling_qty)
    expected_max_sellable, expected_max_sellable_reason = _clamp_value_to_ceiling(
        expected_max_sellable, expected_max_sellable_reason, platform_ceiling_qty
    )
    return max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason


async def compute_max_sellable(
    session: AsyncSession,
    product_id: int,
    variant_id: int | None,
    current_stock: int,
    allocated_qty: int,
    expected_max_buildable: int | None,
    platform_ceiling_qty: int | None = None,
    max_buildable: int | None = None,
) -> tuple[int, str | None, int | None, str | None, int, str | None, list[VariantKittingBomLine]]:
    """Combines free finished-goods stock (current_stock - allocated_qty) with free
    packaging capacity to answer "how many could ship right now" (max_sellable),
    "...eventually, counting on-order supply of both raw materials and packaging"
    (expected_max_sellable), and "...right now, if StockSmith builds to backfill an
    order using only materials already on hand" (theoretical_max_sellable) — plus each's
    binding reason, so callers can explain a low number as "nothing built", "out of
    packaging", or "out of raw materials too". Also returns the resolved kitting BOM,
    each line carrying its own bottleneck, for display alongside the build BOM's
    equivalent. See apply_platform_ceiling for the manual-cap clamp applied here."""
    kitting_capacity, expected_kitting_capacity, kitting_bom = await compute_variant_kitting_capacity(
        session, product_id, variant_id
    )
    free_stock = current_stock - allocated_qty
    max_sellable, max_sellable_reason = combine_max_sellable(free_stock, kitting_capacity)
    expected_max_sellable, expected_max_sellable_reason = combine_expected_max_sellable(
        expected_max_buildable, expected_kitting_capacity
    )
    theoretical_max_sellable, theoretical_max_sellable_reason = combine_theoretical_max_sellable(
        free_stock, max_buildable, kitting_capacity
    )
    max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason = apply_platform_ceiling(
        max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason, platform_ceiling_qty
    )
    theoretical_max_sellable, theoretical_max_sellable_reason = _clamp_value_to_ceiling(
        theoretical_max_sellable, theoretical_max_sellable_reason, platform_ceiling_qty
    )
    return (
        max_sellable,
        max_sellable_reason,
        expected_max_sellable,
        expected_max_sellable_reason,
        theoretical_max_sellable,
        theoretical_max_sellable_reason,
        kitting_bom,
    )


async def get_resolved_kitting_boms_by_variant(
    session: AsyncSession, product_id: int
) -> dict[int, list[VariantKittingBomLine]]:
    """Bulk analog of get_resolved_kitting_bom(variant_id=<each variant>) — resolves
    every variant's effective kitting BOM in one query instead of one query per variant.
    A variant with no kitting BOM at all is simply absent from the returned dict."""
    result = await session.execute(_RESOLVED_PRODUCT_VARIANTS_KITTING_BOM_SQL, {"product_id": product_id})
    boms: dict[int, list[VariantKittingBomLine]] = {}
    for row in result:
        if row.effective_qty_required <= 0:
            continue
        boms.setdefault(row.variant_id, []).append(
            VariantKittingBomLine(
                material_id=row.material_id,
                qty_required=Decimal(row.effective_qty_required),
                replaces_material_id=row.replaces_material_id,
            )
        )
    return boms


async def compute_variants_kitting_capacity_bulk(
    session: AsyncSession, product_id: int, variant_ids: list[int]
) -> dict[int, tuple[int | None, int | None, list[VariantKittingBomLine]]]:
    """Bulk analog of compute_variant_kitting_capacity for every variant_id given (all
    must belong to product_id). 2 queries total regardless of len(variant_ids)."""
    boms_by_variant = await get_resolved_kitting_boms_by_variant(session, product_id)

    all_material_ids = {line.material_id for bom in boms_by_variant.values() for line in bom}
    materials: dict[int, object] = {}
    if all_material_ids:
        rows = await session.execute(
            text(
                f"""
                SELECT m.id, m.current_qty, m.allocated_qty, COALESCE(oo.on_order_qty, 0) AS on_order_qty
                FROM materials m
                LEFT JOIN ({_ON_ORDER_BY_MATERIAL_SUBQUERY}) oo ON oo.material_id = m.id
                WHERE m.id IN :ids
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": list(all_material_ids)},
        )
        materials = {row.id: row for row in rows}

    results: dict[int, tuple[int | None, int | None, list[VariantKittingBomLine]]] = {}
    for variant_id in variant_ids:
        bom = boms_by_variant.get(variant_id, [])
        if not bom:
            results[variant_id] = (None, None, bom)
            continue
        for line in bom:
            m = materials[line.material_id]
            free = Decimal(m.current_qty) - Decimal(m.allocated_qty)
            expected_free = free + Decimal(m.on_order_qty)
            line.line_max_buildable = int(free // line.qty_required)
            line.line_expected_max_buildable = int(expected_free // line.qty_required)
        kitting_capacity = min(line.line_max_buildable for line in bom)
        expected_kitting_capacity = min(line.line_expected_max_buildable for line in bom)
        results[variant_id] = (kitting_capacity, expected_kitting_capacity, bom)
    return results


async def compute_max_sellable_bulk(
    session: AsyncSession,
    product_id: int,
    variants: list[ProductVariant],
    expected_max_buildable_by_variant: dict[int, int | None],
    platform_ceiling_qty: int | None,
    max_buildable_by_variant: dict[int, int | None] | None = None,
) -> dict[int, tuple[int, str | None, int | None, str | None, int, str | None, list[VariantKittingBomLine]]]:
    """Bulk analog of compute_max_sellable for every variant in `variants` (ProductVariant
    ORM rows, all belonging to product_id). Reuses the existing pure helpers
    (combine_max_sellable, combine_expected_max_sellable, combine_theoretical_max_sellable,
    apply_platform_ceiling) unchanged — only the DB-backed kitting-capacity lookup needed
    batching."""
    max_buildable_by_variant = max_buildable_by_variant or {}
    kitting = await compute_variants_kitting_capacity_bulk(session, product_id, [v.id for v in variants])
    results: dict[int, tuple[int, str | None, int | None, str | None, int, str | None, list[VariantKittingBomLine]]] = {}
    for v in variants:
        kitting_capacity, expected_kitting_capacity, kitting_bom = kitting.get(v.id, (None, None, []))
        free_stock = v.current_stock - v.allocated_qty
        max_sellable, max_sellable_reason = combine_max_sellable(free_stock, kitting_capacity)
        expected_max_sellable, expected_max_sellable_reason = combine_expected_max_sellable(
            expected_max_buildable_by_variant.get(v.id), expected_kitting_capacity
        )
        theoretical_max_sellable, theoretical_max_sellable_reason = combine_theoretical_max_sellable(
            free_stock, max_buildable_by_variant.get(v.id), kitting_capacity
        )
        max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason = apply_platform_ceiling(
            max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason, platform_ceiling_qty
        )
        theoretical_max_sellable, theoretical_max_sellable_reason = _clamp_value_to_ceiling(
            theoretical_max_sellable, theoretical_max_sellable_reason, platform_ceiling_qty
        )
        results[v.id] = (
            max_sellable,
            max_sellable_reason,
            expected_max_sellable,
            expected_max_sellable_reason,
            theoretical_max_sellable,
            theoretical_max_sellable_reason,
            kitting_bom,
        )
    return results


async def _compute_auto_kitting_totals(
    session: AsyncSession, lines: list[OrderLine], line_qty: Callable[[OrderLine], int]
) -> dict[int, Decimal]:
    """Sums each line's resolved kitting BOM x line_qty(line) by material — the
    pre-override "default" requirement, before any OrderKittingOverride is applied."""
    auto: dict[int, Decimal] = {}
    for line in lines:
        if line.needs_mapping or line.product_id is None:
            continue
        qty = line_qty(line)
        if qty <= 0:
            continue
        bom = await get_resolved_kitting_bom(session, line.product_id, line.variant_id)
        for bom_line in bom:
            auto[bom_line.material_id] = auto.get(bom_line.material_id, Decimal(0)) + bom_line.qty_required * qty
    return auto


def _apply_kitting_overrides(
    auto: dict[int, Decimal], overrides: list[OrderKittingOverride]
) -> dict[int, Decimal]:
    """Same 3-kind semantics as a variant BOM override (qty override / substitution /
    additive), except here qty_required is an absolute total for the whole order rather
    than a per-unit rate, since an order isn't "built" repeatedly the way a product is."""
    replaced_material_ids = {o.replaces_material_id for o in overrides if o.replaces_material_id is not None}
    result = {material_id: qty for material_id, qty in auto.items() if material_id not in replaced_material_ids}
    for o in overrides:
        result[o.material_id] = Decimal(o.qty_required)
    return {material_id: qty for material_id, qty in result.items() if qty > 0}


async def _compute_kitting_requirement(
    session: AsyncSession, order_id: int, lines: list[OrderLine], line_qty: Callable[[OrderLine], int]
) -> dict[int, Decimal]:
    # Overrides are absolute totals for the whole order, so they only make sense while
    # the order has any activity under this keying (any line with qty > 0). Without this
    # gate, a fully-shipped or fully-cancelled order's reservation target would still
    # include the override qty forever — a phantom reservation that never releases.
    has_activity = any(
        not l.needs_mapping and l.product_id is not None and line_qty(l) > 0 for l in lines
    )
    if not has_activity:
        return {}

    auto = await _compute_auto_kitting_totals(session, lines, line_qty)
    overrides = list(
        (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order_id)))
        .scalars()
    )
    return _apply_kitting_overrides(auto, overrides)


async def reconcile_order_kitting(session: AsyncSession, order: Order) -> None:
    """Recomputes the order's current kitting reservation/consumption targets and diffs
    them against the OrderKittingAllocation ledger, applying only the delta. Called after
    any allocate/ship/cancel/deallocate on the order, or after its kitting overrides
    change — safe to call repeatedly and idempotent given unchanged inputs.

    Reservation target is keyed on each line's allocated-but-not-yet-shipped qty (so it
    naturally drops as shipping consumes it). Consumption target is keyed on shipped_qty
    (monotonic, mirrors how current_stock only ever decrements at ship time for products)
    — consume_delta is only ever nonzero right after a ship, since nothing else changes
    shipped_qty, so the raise below can only trigger from the ship path.

    Reservation shortfalls (at allocate/cancel/deallocate time) never block or raise — a
    grant is simply partial, same as _allocate_line's product stock grant; see
    get_orders_awaiting_packaging for how that shortfall is surfaced instead. Consumption
    shortfalls (at ship time) DO raise — mirrors ship_line's own "No allocated units to
    ship" failure semantics: shipping already enforces physical reality strictly for
    product stock, so a genuinely-missing packaging material fails the same way rather
    than silently under-consuming and leaving the reservation ledger inconsistent.
    """
    lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalars())

    reserved_target = await _compute_kitting_requirement(
        session, order.id, lines, lambda l: l.allocated_qty - l.shipped_qty
    )
    consumed_target = await _compute_kitting_requirement(session, order.id, lines, lambda l: l.shipped_qty)

    ledger_rows = {
        row.material_id: row
        for row in (
            await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
        ).scalars()
    }

    material_ids = set(reserved_target) | set(consumed_target) | set(ledger_rows)
    for material_id in material_ids:
        ledger = ledger_rows.get(material_id)
        current_reserved = Decimal(ledger.reserved_qty) if ledger else Decimal(0)
        current_consumed = Decimal(ledger.consumed_qty) if ledger else Decimal(0)
        target_reserved = reserved_target.get(material_id, Decimal(0))
        target_consumed = consumed_target.get(material_id, Decimal(0))

        material = await session.get(Material, material_id)
        if material is None:
            continue

        # Consumption only ever grows (shipped_qty is monotonic) — physically decrements
        # current_qty via the same MaterialAdjustment + recompute pattern builds.py uses.
        # Release the matching reservation first, in the same in-memory object, so the
        # flush inside recompute_material sees allocated_qty already reduced alongside
        # the new (lower) current_qty — otherwise the two could transiently violate
        # ck_materials_allocated_qty_range before both changes land together.
        consume_delta = target_consumed - current_consumed
        if consume_delta > 0:
            release = min(consume_delta, current_reserved)
            # Post-consumption, current_qty must still cover every OTHER order's
            # outstanding reservation (ck_materials_allocated_qty_range). This order's
            # own reservation is released below, so the bound is:
            #   consume_delta <= current_qty - allocated_qty + release
            # When this order was fully reserved (release == consume_delta) this always
            # holds; it only bites when shipping more than was reserved (a partial grant
            # under shortage) and the unreserved remainder doesn't fit in free stock.
            available = Decimal(material.current_qty) - Decimal(material.allocated_qty) + release
            if consume_delta > available:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Cannot ship — insufficient packaging material '{material.name}' "
                        f"(need {consume_delta}, only {available} on hand and not reserved by other orders)"
                    ),
                )
            material.allocated_qty = max(Decimal(0), Decimal(material.allocated_qty) - release)
            current_reserved -= release

            session.add(
                MaterialAdjustment(
                    material_id=material_id,
                    qty_delta=-consume_delta,
                    reason=f"Order #{order.id} shipped (kitting)",
                    order_id=order.id,
                )
            )
            material = await recompute_material(session, material_id)
            current_consumed += consume_delta

        # Reservation: grant capped by free capacity (partial grant, never blocks/raises —
        # see docstring), or release down to the new (possibly ship-reduced) target.
        reserve_delta = target_reserved - current_reserved
        if reserve_delta > 0:
            free = Decimal(material.current_qty) - Decimal(material.allocated_qty)
            grant = max(Decimal(0), min(reserve_delta, free))
            material.allocated_qty = Decimal(material.allocated_qty) + grant
            current_reserved += grant
        elif reserve_delta < 0:
            release = min(-reserve_delta, current_reserved)
            material.allocated_qty = Decimal(material.allocated_qty) - release
            current_reserved -= release

        if consume_delta != 0 or reserve_delta != 0:
            # Packaging capacity for every OTHER product/variant sharing this material
            # depends on current_qty - allocated_qty (see compute_variant_kitting_
            # capacity) — a reservation change alone (not just consumption) can shift
            # that. Deferred import: avoids a module-load-time cycle (listing_push pulls
            # in kitting.py itself for compute_max_sellable).
            from app.services import listing_push

            await listing_push.enqueue_for_material(session, material_id)

        if ledger is None:
            session.add(
                OrderKittingAllocation(
                    order_id=order.id,
                    material_id=material_id,
                    reserved_qty=current_reserved,
                    consumed_qty=current_consumed,
                )
            )
        else:
            ledger.reserved_qty = current_reserved
            ledger.consumed_qty = current_consumed


async def get_order_kitting_summary(session: AsyncSession, order_id: int) -> OrderKittingSummary:
    """Read-only view for the order-level kitting override editor: the default
    (pre-override) requirement per material based on each line's full ordered_qty (not
    just what's currently allocated/shipped — this is a forward-looking "what will this
    order need" view, independent of allocation state), the effective requirement after
    overrides, current saved overrides, and the ledger's actual reserved/consumed qty for
    transparency."""
    lines = list((await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars())
    auto = await _compute_auto_kitting_totals(session, lines, lambda l: l.ordered_qty)

    overrides = list(
        (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order_id)))
        .scalars()
    )
    effective = _apply_kitting_overrides(auto, overrides)

    ledger_rows = {
        row.material_id: row
        for row in (
            await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order_id))
        ).scalars()
    }

    material_ids = set(auto) | set(effective) | set(ledger_rows)
    materials = {
        m.id: m
        for m in (
            await session.execute(select(Material).where(Material.id.in_(material_ids)))
        ).scalars()
    } if material_ids else {}

    result_lines = [
        OrderKittingRequirementLine(
            material_id=material_id,
            material_name=materials[material_id].name if material_id in materials else "Unknown material",
            auto_qty=auto.get(material_id, Decimal(0)),
            effective_qty=effective.get(material_id, Decimal(0)),
            reserved_qty=Decimal(ledger_rows[material_id].reserved_qty) if material_id in ledger_rows else Decimal(0),
            consumed_qty=Decimal(ledger_rows[material_id].consumed_qty) if material_id in ledger_rows else Decimal(0),
        )
        for material_id in material_ids
    ]
    result_lines.sort(key=lambda l: l.material_name)

    override_lines = [
        OrderKittingOverrideLine(
            material_id=o.material_id, qty_required=Decimal(o.qty_required), replaces_material_id=o.replaces_material_id
        )
        for o in overrides
    ]

    return OrderKittingSummary(overrides=override_lines, lines=result_lines)


async def sync_listing_ceiling_qty(
    session: AsyncSession, product_id: int, variant_id: int | None, ceiling_qty: int | None
) -> None:
    """Writes expected_max_sellable into Listing.ceiling_qty/last_synced_qty/
    last_synced_at — columns already reserved in the schema for exactly this ("the
    future quantity-push phase... not implemented yet"). Local bookkeeping only, no
    outbound call to Etsy; a no-op if this product/variant has no Etsy listing on record."""
    if ceiling_qty is None:
        return
    result = await session.execute(
        select(Listing).where(
            Listing.product_id == product_id,
            Listing.variant_id == variant_id,
            Listing.platform == ListingPlatform.etsy,
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        return
    listing.ceiling_qty = ceiling_qty
    listing.last_synced_qty = ceiling_qty
    listing.last_synced_at = datetime.now(timezone.utc)


async def get_orders_awaiting_packaging(session: AsyncSession) -> list[OrderAwaitingPackaging]:
    """Orders whose kitting reservation was only partially granted due to insufficient
    packaging material — the packaging analog of buildability.get_orders_awaiting_inventory.
    Recomputed live rather than read off the ledger, since OrderKittingAllocation only
    stores what was actually granted, not the target it may have fallen short of."""
    orders = list(
        (
            await session.execute(
                select(Order).where(Order.status.in_([OrderStatus.pending, OrderStatus.allocated]))
            )
        ).scalars()
    )
    if not orders:
        return []

    order_ids = [o.id for o in orders]
    lines_by_order: dict[int, list[OrderLine]] = {}
    for line in (await session.execute(select(OrderLine).where(OrderLine.order_id.in_(order_ids)))).scalars():
        lines_by_order.setdefault(line.order_id, []).append(line)

    ledger_by_order: dict[int, dict[int, OrderKittingAllocation]] = {}
    for row in (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id.in_(order_ids)))
    ).scalars():
        ledger_by_order.setdefault(row.order_id, {})[row.material_id] = row

    shortfalls: list[tuple[Order, int, Decimal]] = []
    material_ids_needed: set[int] = set()
    for order in orders:
        lines = lines_by_order.get(order.id, [])
        if not lines:
            continue
        target = await _compute_kitting_requirement(
            session, order.id, lines, lambda l: l.allocated_qty - l.shipped_qty
        )
        ledger = ledger_by_order.get(order.id, {})
        for material_id, target_qty in target.items():
            reserved = Decimal(ledger[material_id].reserved_qty) if material_id in ledger else Decimal(0)
            if target_qty > reserved:
                shortfalls.append((order, material_id, target_qty - reserved))
                material_ids_needed.add(material_id)

    if not shortfalls:
        return []

    material_names = {
        row.id: row.name
        for row in (
            await session.execute(select(Material.id, Material.name).where(Material.id.in_(material_ids_needed)))
        )
    }

    result = [
        OrderAwaitingPackaging(
            order_id=order.id,
            material_id=material_id,
            material_name=material_names.get(material_id, "Unknown material"),
            short_by=short_by,
            order_placed_at=order.order_placed_at,
        )
        for order, material_id, short_by in shortfalls
    ]
    result.sort(key=lambda r: r.short_by, reverse=True)
    return result
