from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialAdjustment, MaterialAdjustmentMode
from app.services.purchase_sql import ON_ORDER_BY_MATERIAL_SQL
from app.services.validation import validate_qty_for_unit

_ON_ORDER_BY_MATERIAL_SQL = text(ON_ORDER_BY_MATERIAL_SQL)

# Note what this does NOT join: purchases. A receipt row is the whole story of a delivery
# — how much, and when — so the order's status has no say in what a material's stock and
# cost work out to. That is deliberate: status is a denormalisation for the list filter,
# and something derived from receipts should never be derived from it in turn.
#
# The seq tiebreak matters. Receiving a whole order in one go writes several receipts
# sharing a timestamp, and the remainder sweep in the replay below is order-dependent
# within a line — it has to fire on the last receipt of that line, not an arbitrary one.
_MATERIAL_HISTORY_SQL = text(
    """
    SELECT kind, at, qty, total_cost, line_id, line_qty, line_total_cost FROM (
        SELECT 'purchase' AS kind, r.received_at AS at, r.qty AS qty, r.total_cost AS total_cost,
               mp.id AS line_id, mp.qty AS line_qty, mp.total_cost AS line_total_cost,
               r.id AS seq, 0 AS kind_order
        FROM material_purchase_receipts r
        JOIN material_purchases mp ON mp.id = r.purchase_line_id
        WHERE mp.material_id = :material_id

        UNION ALL

        SELECT 'adjustment' AS kind, ma.created_at AS at, ma.qty_delta AS qty, NULL AS total_cost,
               NULL AS line_id, NULL AS line_qty, NULL AS line_total_cost,
               ma.id AS seq, 1 AS kind_order
        FROM material_adjustments ma
        WHERE ma.material_id = :material_id
    ) AS history
    ORDER BY at ASC, kind_order ASC, seq ASC
    """
)


def _receipt_cost_share(
    row,
    receipt_qty: Decimal,
    already_received: Decimal,
    costed_by_line: dict[int, Decimal],
    lines_with_explicit_cost: set[int],
) -> Decimal:
    """What this delivery cost — either what the supplier billed for it, or its share.

    A purchase line carries one total for the whole ordered quantity, so a delivery of
    part of it has to be given a share of that total. Three rules, in order:

    1. The receipt has its own total_cost. Use it. This is the invoice saying something
       other than "a slice of the line", so nothing here should second-guess it.

    2. The receipt completes the line, and no receipt on that line named its own cost.
       Take whatever is left of the line total. This is the remainder sweep, and it earns
       its keep twice over: the shares of a fully-received line add up to the line total
       *exactly*, leaving no stranded pennies from Numeric(14,2) rounding — and the common
       case of one receipt covering the whole line comes through as the line total itself,
       untouched by any division. That last part is what makes the receipts backfill
       reproduce existing average costs bit for bit rather than approximately.

    3. Otherwise, pro-rata on quantity.

    Rule 2 is switched off for a line where any receipt named its own cost, because the
    remainder is then whatever the explicit figures happened to leave and can be negative
    or absurd. The clamp catches the same thing from the other direction: a line total
    edited downwards after part of it was already costed.
    """
    if row.total_cost is not None:
        return Decimal(row.total_cost)

    line_qty = Decimal(row.line_qty)
    line_total_cost = Decimal(row.line_total_cost)
    if line_qty <= 0:
        return Decimal(0)

    completes_line = already_received + receipt_qty >= line_qty
    if completes_line and row.line_id not in lines_with_explicit_cost:
        share = line_total_cost - costed_by_line.get(row.line_id, Decimal(0))
    else:
        share = line_total_cost * receipt_qty / line_qty

    return share if share > 0 else Decimal(0)


async def recompute_material(session: AsyncSession, material_id: int) -> Material:
    """Rebuilds a material's current_qty/avg_unit_cost from scratch by replaying its
    full history (purchase receipts + adjustments) in chronological order.

    Each receipt lands at its own date, which is the point of them: a line ordered 10 and
    delivered 6-then-4 contributes twice, weeks apart, and the weighted average picks each
    up against whatever stock was on hand at the time. A single received_at on the order
    could only ever pretend both arrived together.

    current_qty/avg_unit_cost are purely derived/cached columns — nothing else should
    ever assign to them directly. This gets called after any mutation that could affect
    a material's history: purchase line create/edit/delete, a receipt being recorded or
    reversed, or a new adjustment.

    Does NOT commit — flushes only, so callers can batch multiple recomputes (or an
    insert alongside a recompute, e.g. create_adjustment below) into one transaction and
    roll the whole thing back together if a post-recompute check fails.
    """
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    # Raw SQL (unlike an ORM-mapped select()) doesn't trigger autoflush, so a
    # just-added-but-unflushed MaterialAdjustment/MaterialPurchaseReceipt in this same
    # transaction would silently be invisible to the history query below without this.
    await session.flush()
    rows = list(await session.execute(_MATERIAL_HISTORY_SQL, {"material_id": material_id}))

    # Which lines have a receipt carrying its own cost. Needed before the replay starts,
    # because it decides how the *first* receipt of a line is priced, not just the last.
    lines_with_explicit_cost = {
        row.line_id for row in rows if row.kind == "purchase" and row.total_cost is not None
    }

    qty = Decimal(0)
    avg_cost = Decimal(0)
    received_by_line: dict[int, Decimal] = {}
    costed_by_line: dict[int, Decimal] = {}
    for row in rows:
        if row.kind == "purchase":
            receipt_qty = Decimal(row.qty)
            already_received = received_by_line.get(row.line_id, Decimal(0))
            share = _receipt_cost_share(row, receipt_qty, already_received, costed_by_line, lines_with_explicit_cost)
            received_by_line[row.line_id] = already_received + receipt_qty
            costed_by_line[row.line_id] = costed_by_line.get(row.line_id, Decimal(0)) + share

            new_qty = qty + receipt_qty
            avg_cost = (qty * avg_cost + share) / new_qty if new_qty > 0 else Decimal(0)
            qty = new_qty
        else:  # adjustment — qty delta only, never touches avg_cost
            qty = qty + Decimal(row.qty)

    material.current_qty = qty
    material.avg_unit_cost = avg_cost
    await session.flush()

    # Deferred import: avoids a module-load-time cycle (listing_push pulls in kitting.py,
    # which is otherwise unrelated to costing.py). Fans this material's changed qty out
    # to every product/variant whose build or kitting BOM references it — the material-
    # consumption half of the allocated/consumed-means-unavailable rule (see
    # docs/plan-marketplace-integrations.md Section 1d). Every current caller of
    # recompute_material (purchases, adjustments, CSV import, kitting ship-consumption)
    # gets this for free rather than needing its own trigger.
    from app.services import listing_push

    await listing_push.enqueue_for_material(session, material_id)

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

    # A "set" IS a physical count — it's what this mode has always meant (see
    # MaterialAdjustment's docstring), so it restarts the counting clock and the item stops
    # showing as due. An "adjust" deliberately does not: a signed delta for breakage says
    # what changed, not that the resulting total was ever verified, and dating it as a count
    # would vouch for a figure nobody looked at.
    #
    # Set after the negative-qty checks so a rejected adjustment doesn't leave the date
    # moved, and before the commit so both land in one transaction.
    if mode == MaterialAdjustmentMode.set:
        material.last_stock_take_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(material)
    return material


async def get_on_order_qty_by_material(session: AsyncSession) -> dict[int, Decimal]:
    result = await session.execute(_ON_ORDER_BY_MATERIAL_SQL)
    return {row.material_id: Decimal(row.on_order_qty) for row in result}
