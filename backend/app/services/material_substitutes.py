"""Material-level substitution fallbacks (see app.models.material_substitute).

Kept deliberately small: this only ever reads curated rows and writes usage-audit rows.
It never decides whether a substitute is "valid" beyond the identity/duplicate checks the
router already enforces on write — matching a shortage to a person-chosen fallback, and
nothing more.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.material_substitute import MaterialSubstitute, MaterialSubstituteUsage
from app.schemas.material_substitute import SubstituteSuggestion
from app.services.purchase_sql import ON_ORDER_BY_MATERIAL_SQL

# Free (and expected-free) stock of every active, human-curated fallback for a material,
# summed per material. Build and packaging capacity treat a material's fallbacks as extra
# stock of that material — a box that's short but has a fallback with plenty on the shelf
# doesn't cap what can be sold — while ALSO reporting the material-only figure beside it
# (the *_incl_fallbacks fields on BOM lines, products and variants), so a reader can tell
# "10 from the BOM as written, 20 counting fallbacks". Suggestions at the shortage points
# (buildability lines, kitting lines, orders awaiting packaging) still key off the
# material's OWN stock: a line whose shelf is empty but whose fallback covers it is
# exactly the line that needs a person to pick which fallback to actually use.
#
# Free stock (current - allocated) on both sides, even though build capacity measures the
# material itself gross: a fallback already reserved to an order's packaging can't be
# lent to a build.
#
# Limitations, accepted deliberately: one level only (a fallback's own fallbacks don't
# chain in), and a fallback that is also a direct line of the same BOM — or a fallback for
# two lines of it — is counted once per line rather than shared out, the same per-line
# min() approximation the base capacity already makes for a material used on two lines.
FALLBACK_POOL_BY_MATERIAL_SQL = f"""
    SELECT ms.material_id,
           SUM(s.current_qty - s.allocated_qty) AS fallback_free_qty,
           SUM(s.current_qty - s.allocated_qty + COALESCE(soo.on_order_qty, 0)) AS fallback_expected_free_qty
    FROM material_substitutes ms
    JOIN materials s ON s.id = ms.substitute_material_id
    LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) soo ON soo.material_id = s.id
    WHERE ms.is_active = true
    GROUP BY ms.material_id
"""
from app.services.purchase_sql import ON_ORDER_BY_MATERIAL_SQL

# Free (and expected-free) stock of every active, human-curated fallback for a material,
# summed per material. Build and packaging capacity treat a material's fallbacks as extra
# stock of that material — a box that's short but has a fallback with plenty on the shelf
# doesn't cap what can be sold — while ALSO reporting the material-only figure beside it
# (the *_incl_fallbacks fields on BOM lines, products and variants), so a reader can tell
# "10 from the BOM as written, 20 counting fallbacks". Suggestions at the shortage points
# (buildability lines, kitting lines, orders awaiting packaging) still key off the
# material's OWN stock: a line whose shelf is empty but whose fallback covers it is
# exactly the line that needs a person to pick which fallback to actually use.
#
# Free stock (current - allocated) on both sides, even though build capacity measures the
# material itself gross: a fallback already reserved to an order's packaging can't be
# lent to a build.
#
# Limitations, accepted deliberately: one level only (a fallback's own fallbacks don't
# chain in), and a fallback that is also a direct line of the same BOM — or a fallback for
# two lines of it — is counted once per line rather than shared out, the same per-line
# min() approximation the base capacity already makes for a material used on two lines.
FALLBACK_POOL_BY_MATERIAL_SQL = f"""
    SELECT ms.material_id,
           SUM(s.current_qty - s.allocated_qty) AS fallback_free_qty,
           SUM(s.current_qty - s.allocated_qty + COALESCE(soo.on_order_qty, 0)) AS fallback_expected_free_qty
    FROM material_substitutes ms
    JOIN materials s ON s.id = ms.substitute_material_id
    LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) soo ON soo.material_id = s.id
    WHERE ms.is_active = true
    GROUP BY ms.material_id
"""
from app.services.purchase_sql import ON_ORDER_BY_MATERIAL_SQL

# Free (and expected-free) stock of every active, human-curated fallback for a material,
# summed per material. Build and packaging capacity treat a material's fallbacks as extra
# stock of that material — a box that's short but has a fallback with plenty on the shelf
# doesn't cap what can be sold — while ALSO reporting the material-only figure beside it
# (the *_incl_fallbacks fields on BOM lines, products and variants), so a reader can tell
# "10 from the BOM as written, 20 counting fallbacks". Suggestions at the shortage points
# (buildability lines, kitting lines, orders awaiting packaging) still key off the
# material's OWN stock: a line whose shelf is empty but whose fallback covers it is
# exactly the line that needs a person to pick which fallback to actually use.
#
# Free stock (current - allocated) on both sides, even though build capacity measures the
# material itself gross: a fallback already reserved to an order's packaging can't be
# lent to a build.
#
# Limitations, accepted deliberately: one level only (a fallback's own fallbacks don't
# chain in), and a fallback that is also a direct line of the same BOM — or a fallback for
# two lines of it — is counted once per line rather than shared out, the same per-line
# min() approximation the base capacity already makes for a material used on two lines.
FALLBACK_POOL_BY_MATERIAL_SQL = f"""
    SELECT ms.material_id,
           SUM(s.current_qty - s.allocated_qty) AS fallback_free_qty,
           SUM(s.current_qty - s.allocated_qty + COALESCE(soo.on_order_qty, 0)) AS fallback_expected_free_qty
    FROM material_substitutes ms
    JOIN materials s ON s.id = ms.substitute_material_id
    LEFT JOIN ({ON_ORDER_BY_MATERIAL_SQL}) soo ON soo.material_id = s.id
    WHERE ms.is_active = true
    GROUP BY ms.material_id
"""


def _to_suggestion(sub: MaterialSubstitute, material: Material) -> SubstituteSuggestion:
    return SubstituteSuggestion(
        material_id=sub.substitute_material_id,
        material_name=material.name,
        rank=sub.rank,
        notes=sub.notes,
        available_qty=max(Decimal(0), Decimal(material.current_qty) - Decimal(material.allocated_qty)),
    )


async def get_ranked_substitutes(session: AsyncSession, material_id: int) -> list[SubstituteSuggestion]:
    """Active, human-curated fallbacks for one material, ranked lowest-rank-first (ties
    broken by id, i.e. declaration order)."""
    by_material = await get_ranked_substitutes_by_material(session, {material_id})
    return by_material.get(material_id, [])


async def get_ranked_substitutes_by_material(
    session: AsyncSession, material_ids: set[int]
) -> dict[int, list[SubstituteSuggestion]]:
    """Bulk analog of get_ranked_substitutes, for the shortage scans in buildability.py
    and services/kitting.py, which check many materials at once — one query instead of
    one per shortage."""
    if not material_ids:
        return {}
    result = await session.execute(
        select(MaterialSubstitute, Material)
        .join(Material, Material.id == MaterialSubstitute.substitute_material_id)
        .where(MaterialSubstitute.material_id.in_(material_ids), MaterialSubstitute.is_active.is_(True))
        .order_by(MaterialSubstitute.material_id, MaterialSubstitute.rank, MaterialSubstitute.id)
    )
    by_material: dict[int, list[SubstituteSuggestion]] = {}
    for sub, material in result.all():
        by_material.setdefault(sub.material_id, []).append(_to_suggestion(sub, material))
    return by_material


async def record_substitute_usage(
    session: AsyncSession,
    *,
    material_id: int,
    substitute_material_id: int,
    qty: Decimal | None,
    order_id: int | None,
    build_id: int | None,
    notes: str | None,
    created_by: str | None,
) -> MaterialSubstituteUsage:
    """Logs that a person chose to use `substitute_material_id` in place of
    `material_id`, for the given order/build. Purely a traceability record — it never
    moves stock itself; whatever build/kitting flow actually consumes
    substitute_material_id logs that movement the normal way (MaterialAdjustment)."""
    usage = MaterialSubstituteUsage(
        material_id=material_id,
        substitute_material_id=substitute_material_id,
        qty=qty,
        order_id=order_id,
        build_id=build_id,
        notes=notes,
        created_by=created_by,
    )
    session.add(usage)
    await session.commit()
    await session.refresh(usage)
    return usage
