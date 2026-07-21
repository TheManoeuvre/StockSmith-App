from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialAdjustment, MaterialAdjustmentMode
from app.services.validation import validate_qty_for_unit

_ON_ORDER_BY_MATERIAL_SQL = text(
    """
    SELECT mp.material_id, SUM(mp.qty) AS on_order_qty
    FROM material_purchases mp
    JOIN purchases p ON p.id = mp.purchase_id
    WHERE p.status = 'ordered'
    GROUP BY mp.material_id
    """
)

_MATERIAL_HISTORY_SQL = text(
    """
    SELECT kind, at, qty, total_cost FROM (
        SELECT 'purchase' AS kind, p.received_at AS at, mp.qty AS qty, mp.total_cost AS total_cost, 0 AS kind_order
        FROM material_purchases mp
        JOIN purchases p ON p.id = mp.purchase_id
        WHERE mp.material_id = :material_id AND p.status = 'received'

        UNION ALL

        SELECT 'adjustment' AS kind, ma.created_at AS at, ma.qty_delta AS qty, NULL AS total_cost, 1 AS kind_order
        FROM material_adjustments ma
        WHERE ma.material_id = :material_id
    ) AS history
    ORDER BY at ASC, kind_order ASC
    """
)


async def recompute_material(session: AsyncSession, material_id: int) -> Material:
    """Rebuilds a material's current_qty/avg_unit_cost from scratch by replaying its
    full history (received purchase lines + adjustments) in chronological order.

    current_qty/avg_unit_cost are purely derived/cached columns — nothing else should
    ever assign to them directly. This gets called after any mutation that could affect
    a material's history: purchase line create/edit/delete, a purchase's ordered<->received
    transition, or a new adjustment.

    Does NOT commit — flushes only, so callers can batch multiple recomputes (or an
    insert alongside a recompute, e.g. create_adjustment below) into one transaction and
    roll the whole thing back together if a post-recompute check fails.
    """
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    # Raw SQL (unlike an ORM-mapped select()) doesn't trigger autoflush, so a
    # just-added-but-unflushed MaterialAdjustment/MaterialPurchase in this same
    # transaction would silently be invisible to the history query below without this.
    await session.flush()
    rows = await session.execute(_MATERIAL_HISTORY_SQL, {"material_id": material_id})

    qty = Decimal(0)
    avg_cost = Decimal(0)
    for row in rows:
        if row.kind == "purchase":
            new_qty = qty + Decimal(row.qty)
            avg_cost = (qty * avg_cost + Decimal(row.total_cost)) / new_qty if new_qty > 0 else Decimal(0)
            qty = new_qty
        else:  # adjustment — qty delta only, never touches avg_cost
            qty = qty + Decimal(row.qty)

    material.current_qty = qty
    material.avg_unit_cost = avg_cost
    await session.flush()
    return material


async def recompute_materials(session: AsyncSession, material_ids: set[int]) -> None:
    """Batch wrapper — call once per distinct material affected by a single mutation
    (e.g. a purchase with several lines, or an edit that changed a line's material).
    Does not commit; caller commits once after all recomputes succeed."""
    for material_id in material_ids:
        await recompute_material(session, material_id)

    # Deferred import: avoids a module-load-time cycle (pricing.py pulls in
    # buildability.py, which is otherwise unrelated to costing.py).
    from app.services.pricing import check_and_snapshot_for_materials

    await check_and_snapshot_for_materials(session, material_ids)


async def create_adjustment(
    session: AsyncSession, material_id: int, mode: MaterialAdjustmentMode, value: Decimal, reason: str
) -> Material:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    validate_qty_for_unit(value, material.unit, "value")

    if mode == MaterialAdjustmentMode.set:
        qty_delta = value - Decimal(material.current_qty)
        target_qty = value
    else:
        qty_delta = value
        target_qty = None

    session.add(
        MaterialAdjustment(
            material_id=material_id, mode=mode, qty_delta=qty_delta, target_qty=target_qty, reason=reason
        )
    )
    try:
        material = await recompute_material(session, material_id)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Adjustment would make current_qty negative"
        )
    if material.current_qty < 0:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Adjustment would make current_qty negative"
        )

    await session.commit()
    await session.refresh(material)
    return material


async def get_on_order_qty_by_material(session: AsyncSession) -> dict[int, Decimal]:
    result = await session.execute(_ON_ORDER_BY_MATERIAL_SQL)
    return {row.material_id: Decimal(row.on_order_qty) for row in result}
