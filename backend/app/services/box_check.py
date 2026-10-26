"""Box check: a person confirms which parcel box(es) an order actually used.

Why this exists: auto_apply_multiunit_kitting_override caps packaging to one per ORDER, per
material. That is right for labels and tape but wrong for boxes: an order whose items need two
different box sizes keeps both, and both get reserved and consumed. Rather than guess which box
was really used, an order whose items resolve to two or more different boxes is held until a
person picks.

A material is a "box" when its Material Category has kitting_check_duplicates. The candidates
for an order are the flagged materials in its auto-computed kitting requirement (before any
override), so they stay stable however the person edits quantities.

Three states, one function each:

* Before shipping, confirm_boxes() writes the chosen quantities as order kitting overrides
  (zero for a candidate that wasn't used) and stamps the order as checked. The stamp stores the
  candidate set, so a later change to the order's items that changes the candidates no longer
  matches it and the order needs checking again — no explicit reset needed.
* After shipping, correct_shipped_boxes() rewrites the kitting ledger itself and moves stock to
  match. The ledger is where Kitting COGS is read from (kitting.get_kitting_cogs_by_order), so
  net profit follows on its own; nothing else needs recomputing. It deliberately does NOT call
  reconcile_order_kitting: that would treat any drift in the ledger as new consumption.
* mark_reviewed() on a shipped order records "looked at it, it's right" and nothing else.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kitting import OrderKittingAllocation, OrderKittingOverride
from app.models.material import Material, MaterialAdjustment
from app.models.material_category import MaterialCategory
from app.models.order import Order, OrderLine, OrderStatus
from app.schemas.box_check import (
    BoxCheckBoxRead,
    BoxCheckChangeRead,
    BoxCheckConfirm,
    BoxCheckPreview,
    BoxCheckRead,
)
from app.services.costing import recompute_material
from app.services.kitting import _apply_kitting_overrides, _compute_auto_kitting_totals, reconcile_order_kitting

# Prefix is what lets confirm_boxes recognise (and clear) the flag the sync set, without
# touching an unrelated sync_issue.
BOX_CHECK_SYNC_ISSUE_PREFIX = "Box check needed"
BOX_CHECK_SYNC_ISSUE = (
    BOX_CHECK_SYNC_ISSUE_PREFIX + ": {platform} shows this order as shipped, but its items use more than one "
    "parcel box. Confirm which box was used on the order page, then ship it."
)

_ACTIVE = (OrderStatus.pending, OrderStatus.allocated)


def signature(box_ids: set[int]) -> str:
    return ",".join(str(i) for i in sorted(box_ids))


async def flagged_material_ids(session: AsyncSession) -> set[int]:
    """Every material whose category carries the kitting-check flag."""
    rows = await session.execute(
        select(Material.id)
        .join(MaterialCategory, Material.category_id == MaterialCategory.id)
        .where(MaterialCategory.kitting_check_duplicates.is_(True))
    )
    return set(rows.scalars())


async def _get_lines(session: AsyncSession, order_id: int) -> list[OrderLine]:
    return list((await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars())


async def candidate_box_ids(session: AsyncSession, lines: list[OrderLine], flagged: set[int]) -> set[int]:
    if not flagged:
        return set()
    auto = await _compute_auto_kitting_totals(session, lines, lambda l: l.ordered_qty)
    return {material_id for material_id in auto if material_id in flagged}


async def _consumed_box_rows(
    session: AsyncSession, order_id: int, flagged: set[int]
) -> dict[int, OrderKittingAllocation]:
    """Ledger rows for flagged materials that have physically left stock for this order."""
    if not flagged:
        return {}
    rows = await session.execute(
        select(OrderKittingAllocation).where(
            OrderKittingAllocation.order_id == order_id,
            OrderKittingAllocation.material_id.in_(flagged),
            OrderKittingAllocation.consumed_qty > 0,
        )
    )
    return {row.material_id: row for row in rows.scalars()}


async def order_needs_box_check(
    session: AsyncSession, order: Order, flagged: set[int] | None = None
) -> bool:
    """True for an order still to ship whose items resolve to two or more different boxes and
    which nobody has confirmed for exactly that set of boxes."""
    if order.status not in _ACTIVE:
        return False
    flagged = await flagged_material_ids(session) if flagged is None else flagged
    candidates = await candidate_box_ids(session, await _get_lines(session, order.id), flagged)
    return len(candidates) >= 2 and order.box_check_signature != signature(candidates)


async def assert_can_ship(session: AsyncSession, order: Order) -> None:
    if await order_needs_box_check(session, order):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This order uses more than one parcel box. Confirm which box was used before shipping.",
        )


async def _shipped_multi_box_order_ids(session: AsyncSession, order_ids: list[int]) -> set[int]:
    """Orders whose ledger consumed two or more different flagged boxes — one grouped query."""
    if not order_ids:
        return set()
    rows = await session.execute(
        select(OrderKittingAllocation.order_id)
        .join(Material, Material.id == OrderKittingAllocation.material_id)
        .join(MaterialCategory, MaterialCategory.id == Material.category_id)
        .where(
            OrderKittingAllocation.order_id.in_(order_ids),
            OrderKittingAllocation.consumed_qty > 0,
            MaterialCategory.kitting_check_duplicates.is_(True),
        )
        .group_by(OrderKittingAllocation.order_id)
        .having(func.count(distinct(OrderKittingAllocation.material_id)) >= 2)
    )
    return set(rows.scalars())


async def get_box_check_flags(session: AsyncSession, orders: list[Order]) -> dict[int, bool]:
    """needs_box_check for a whole page of orders. Active orders are resolved one by one (the
    candidate boxes come from each line's kitting BOM); shipped ones in a single query."""
    flags = {order.id: False for order in orders}
    flagged = await flagged_material_ids(session)
    if not flagged:
        return flags
    for order in orders:
        if order.status in _ACTIVE:
            flags[order.id] = await order_needs_box_check(session, order, flagged)
    shipped = [o for o in orders if o.status == OrderStatus.shipped and o.box_check_confirmed_at is None]
    multi = await _shipped_multi_box_order_ids(session, [o.id for o in shipped])
    for order in shipped:
        flags[order.id] = order.id in multi
    return flags


async def order_ids_needing_box_check(session: AsyncSession) -> list[int]:
    """Every order id that needs a check, for the orders list filter."""
    flagged = await flagged_material_ids(session)
    if not flagged:
        return []
    active = list((await session.execute(select(Order).where(Order.status.in_(_ACTIVE)))).scalars())
    ids = [o.id for o in active if await order_needs_box_check(session, o, flagged)]
    shipped_ids = list(
        (
            await session.execute(
                select(Order.id).where(Order.status == OrderStatus.shipped, Order.box_check_confirmed_at.is_(None))
            )
        ).scalars()
    )
    return ids + sorted(await _shipped_multi_box_order_ids(session, shipped_ids))


@dataclass
class _Row:
    material: Material
    current: Decimal
    new: Decimal
    unit_cost: Decimal
    is_candidate: bool


def _mode(order: Order) -> str:
    if order.status in _ACTIVE:
        return "pre_ship"
    if order.status == OrderStatus.shipped:
        return "shipped"
    return "none"


async def _unit_costs(session: AsyncSession, order_id: int, material_ids: set[int]) -> dict[int, Decimal]:
    """Frozen ledger cost where the order has one, else the material's live average — the same
    precedence kitting.get_order_kitting_summary uses."""
    if not material_ids:
        return {}
    costs = {
        m.id: Decimal(m.avg_unit_cost)
        for m in (await session.execute(select(Material).where(Material.id.in_(material_ids)))).scalars()
    }
    ledger = (
        await session.execute(
            select(OrderKittingAllocation).where(
                OrderKittingAllocation.order_id == order_id, OrderKittingAllocation.material_id.in_(material_ids)
            )
        )
    ).scalars()
    for row in ledger:
        if row.unit_cost_snapshot is not None:
            costs[row.material_id] = Decimal(row.unit_cost_snapshot)
    return costs


async def get_box_check(session: AsyncSession, order: Order) -> BoxCheckRead:
    mode = _mode(order)
    flagged = await flagged_material_ids(session)
    if mode == "none" or not flagged:
        return BoxCheckRead(mode=mode, applies=False, needs_check=False, confirmed_at=None, boxes=[])

    lines = await _get_lines(session, order.id)
    candidates = await candidate_box_ids(session, lines, flagged)
    if mode == "shipped":
        current = {mid: Decimal(row.consumed_qty) for mid, row in (await _consumed_box_rows(session, order.id, flagged)).items()}
        applies = bool(current)
        needs_check = (
            order.box_check_confirmed_at is None
            and order.id in await _shipped_multi_box_order_ids(session, [order.id])
        )
    else:
        overrides = list(
            (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))).scalars()
        )
        auto = await _compute_auto_kitting_totals(session, lines, lambda l: l.ordered_qty)
        effective = _apply_kitting_overrides(auto, overrides)
        current = {mid: qty for mid, qty in effective.items() if mid in flagged}
        for mid in candidates:
            current.setdefault(mid, Decimal(0))
        applies = len(candidates) >= 2
        needs_check = applies and order.box_check_signature != signature(candidates)

    ids = set(current)
    materials = {
        m.id: m for m in (await session.execute(select(Material).where(Material.id.in_(ids)))).scalars()
    } if ids else {}
    costs = await _unit_costs(session, order.id, ids)
    boxes = [
        BoxCheckBoxRead(
            material_id=mid,
            material_name=materials[mid].name,
            is_candidate=mid in candidates,
            qty=qty,
            unit_cost=costs.get(mid, Decimal(0)),
            free_stock=Decimal(materials[mid].current_qty) - Decimal(materials[mid].allocated_qty),
        )
        for mid, qty in current.items()
        if mid in materials
    ]
    boxes.sort(key=lambda b: b.material_name)
    return BoxCheckRead(
        mode=mode,
        applies=applies,
        needs_check=needs_check,
        confirmed_at=order.box_check_confirmed_at,
        boxes=boxes,
    )


async def _validate_selection(session: AsyncSession, payload: BoxCheckConfirm, flagged: set[int]) -> dict[int, Decimal]:
    selected = {b.material_id: Decimal(b.qty) for b in payload.boxes}
    not_boxes = [mid for mid in selected if mid not in flagged]
    if not_boxes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only materials in a category marked 'Kitting: check when an order has more than one' can be chosen as boxes",
        )
    return selected


async def _rows(
    session: AsyncSession, order: Order, selected: dict[int, Decimal], flagged: set[int]
) -> list[_Row]:
    """Every box the change touches: what the order has now, plus what the person picked."""
    mode = _mode(order)
    lines = await _get_lines(session, order.id)
    candidates = await candidate_box_ids(session, lines, flagged)
    if mode == "shipped":
        current = {mid: Decimal(r.consumed_qty) for mid, r in (await _consumed_box_rows(session, order.id, flagged)).items()}
    else:
        overrides = list(
            (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))).scalars()
        )
        auto = await _compute_auto_kitting_totals(session, lines, lambda l: l.ordered_qty)
        current = {mid: q for mid, q in _apply_kitting_overrides(auto, overrides).items() if mid in flagged}
    ids = set(current) | set(selected) | candidates
    materials = {m.id: m for m in (await session.execute(select(Material).where(Material.id.in_(ids)))).scalars()}
    costs = await _unit_costs(session, order.id, ids)
    return [
        _Row(
            material=materials[mid],
            current=current.get(mid, Decimal(0)),
            new=selected.get(mid, Decimal(0)),
            unit_cost=costs.get(mid, Decimal(0)),
            is_candidate=mid in candidates,
        )
        for mid in sorted(ids, key=lambda i: materials[i].name)
    ]


def _stock_effect(order: Order, row: _Row, return_to_stock: bool) -> Decimal:
    if _mode(order) != "shipped":
        return Decimal(0)
    delta = row.current - row.new
    if delta > 0 and not return_to_stock:
        return Decimal(0)
    return delta


async def preview_box_change(session: AsyncSession, order: Order, payload: BoxCheckConfirm) -> BoxCheckPreview:
    _require_changeable(order)
    flagged = await flagged_material_ids(session)
    selected = await _validate_selection(session, payload, flagged)
    rows = await _rows(session, order, selected, flagged)
    before = sum((r.current * r.unit_cost for r in rows), start=Decimal(0))
    after = sum((r.new * r.unit_cost for r in rows), start=Decimal(0))
    warnings = []
    for r in rows:
        effect = _stock_effect(order, r, payload.return_to_stock)
        free = Decimal(r.material.current_qty) - Decimal(r.material.allocated_qty)
        if effect < 0 and -effect > free:
            warnings.append(f"Not enough {r.material.name} in stock (need {-effect}, {free} free)")
    return BoxCheckPreview(
        changes=[
            BoxCheckChangeRead(
                material_id=r.material.id,
                material_name=r.material.name,
                current_qty=r.current,
                new_qty=r.new,
                stock_effect=_stock_effect(order, r, payload.return_to_stock),
            )
            for r in rows
            if r.current != r.new
        ],
        kitting_cost_before=before,
        kitting_cost_after=after,
        net_profit_delta=before - after,
        warnings=warnings,
    )


def _require_changeable(order: Order) -> None:
    if _mode(order) == "none":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change the boxes on a cancelled order")


async def _upsert_overrides(session: AsyncSession, order: Order, rows: list[_Row]) -> None:
    existing = {
        o.material_id: o
        for o in (
            await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
        ).scalars()
    }
    for row in rows:
        override = existing.get(row.material.id)
        if override is None:
            session.add(OrderKittingOverride(order_id=order.id, material_id=row.material.id, qty_required=row.new))
        else:
            override.qty_required = row.new


def _stamp(order: Order, candidates: set[int]) -> None:
    order.box_check_signature = signature(candidates)
    order.box_check_confirmed_at = datetime.now(timezone.utc)
    if order.sync_issue is not None and order.sync_issue.startswith(BOX_CHECK_SYNC_ISSUE_PREFIX):
        order.sync_issue = None


async def confirm_boxes(session: AsyncSession, order: Order, payload: BoxCheckConfirm) -> None:
    """Records which boxes were used. Does not commit."""
    _require_changeable(order)
    flagged = await flagged_material_ids(session)
    selected = await _validate_selection(session, payload, flagged)
    rows = await _rows(session, order, selected, flagged)
    candidates = await candidate_box_ids(session, await _get_lines(session, order.id), flagged)

    if _mode(order) == "shipped":
        await _correct_shipped(session, order, rows, payload.return_to_stock)
    else:
        consumed = await _consumed_box_rows(session, order.id, flagged)
        for row in rows:
            ledger = consumed.get(row.material.id)
            if ledger is not None and row.new < Decimal(ledger.consumed_qty):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"{row.material.name}: {ledger.consumed_qty} already shipped with this order. "
                        "Correct the boxes once the order is fully shipped."
                    ),
                )
        await _upsert_overrides(session, order, rows)
        await session.flush()
        await reconcile_order_kitting(session, order)
    _stamp(order, candidates)


async def _correct_shipped(session: AsyncSession, order: Order, rows: list[_Row], return_to_stock: bool) -> None:
    """Rewrites the ledger to the boxes actually used and moves stock to match. Every check
    runs before the first write so a refusal leaves nothing half-applied."""
    for row in rows:
        effect = _stock_effect(order, row, return_to_stock)
        free = Decimal(row.material.current_qty) - Decimal(row.material.allocated_qty)
        if effect < 0 and -effect > free:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot take {-effect} × {row.material.name} out of stock: "
                    f"only {free} on hand and not reserved by other orders"
                ),
            )

    ledger_rows = {
        r.material_id: r
        for r in (
            await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
        ).scalars()
    }
    for row in rows:
        if row.current == row.new:
            continue
        material_id = row.material.id
        ledger = ledger_rows.get(material_id)
        if ledger is None:
            ledger = OrderKittingAllocation(order_id=order.id, material_id=material_id, reserved_qty=0, consumed_qty=0)
            session.add(ledger)
        # Freeze the cost the first time this material is consumed, as reconcile does — so a
        # box added by correction is costed at today's price and then stays put.
        if ledger.unit_cost_snapshot is None:
            ledger.unit_cost_snapshot = Decimal(row.material.avg_unit_cost)
        effect = _stock_effect(order, row, return_to_stock)
        if effect != 0:
            session.add(
                MaterialAdjustment(
                    material_id=material_id,
                    qty_delta=effect,
                    reason=(
                        f"Order #{order.id} box correction: "
                        + ("returned to stock" if effect > 0 else "additional box used")
                    ),
                    order_id=order.id,
                )
            )
            await recompute_material(session, material_id)
        ledger.consumed_qty = row.new
    await _upsert_overrides(session, order, rows)
    await session.flush()


async def mark_reviewed(session: AsyncSession, order: Order) -> None:
    """A shipped order checked and found right: stamp it so it leaves the filter, change nothing."""
    if order.status != OrderStatus.shipped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only a shipped order can be marked reviewed. Choose the box and confirm instead.",
        )
    order.box_check_confirmed_at = datetime.now(timezone.utc)
