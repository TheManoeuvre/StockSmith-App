"""Counting what a place holds, inside a stock take (docs/plan-locations.md §7.1).

A by-location take has one line per item per place. A count is a statement about one *placement*,
not the item's total: an item kept in three places, counted in one, cannot have its total "set". So
a line is judged against what its own place held when the take started, and applying it moves the
placement to the counted figure and the item's total by only what the count proves:

* fewer than expected: the whole shortfall comes off the total;
* more than expected: found stock comes out of the item's Unassigned first, because stock that was
  never placed has to be somewhere, and only the excess over that raises the total. Without this
  the first count after placing nothing would double every item.

If the placement is the item's only one and nothing is Unassigned the count covers the whole item,
so it is applied as a "set" and dates the item; anything else is an "adjust" and does not (a partial
count says nothing about the rest, the same principle as a blank line).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location, PlacementEvent, PlacementEventKind, StockPlacement
from app.models.material import Material, MaterialAdjustmentMode
from app.models.product import Product
from app.models.stock_adjustment import StockAdjustmentMode
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus
from app.models.variant import ProductVariant
from app.services import locations as locs
from app.services import placements
from app.services.costing import create_adjustment
from app.services.placement_hooks import ItemRef, following_paused
from app.services.stock_adjustments import create_stock_adjustment

ZERO = Decimal(0)


def _bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _qty(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def _ref(line: StockTakeLine) -> ItemRef:
    return ItemRef(material_id=line.material_id, product_id=line.product_id, variant_id=line.variant_id)


@dataclass
class _Plan:
    conflict: str | None = None
    total_change: Decimal = ZERO
    full_count: bool = False
    here: StockPlacement | None = None
    rows: list[StockPlacement] = field(default_factory=list)


async def _plan_line(session: AsyncSession, line: StockTakeLine) -> _Plan:
    """Works out what applying the line would do, and refuses it (as a conflict) if it shouldn't."""
    ref = _ref(line)
    item = await placements.resolve_item(session, ref)
    rows = await placements.item_placements(session, ref)
    here = next((r for r in rows if r.location_id == line.location_id), None)
    current = Decimal(here.qty or 0) if here is not None else ZERO
    expected = Decimal(line.expected_qty)
    counted = Decimal(line.counted_qty)
    plan = _Plan(here=here, rows=rows)

    if current != expected:
        plan.conflict = f"Stock moved here since this stock take started: was {_qty(expected)}, now {_qty(current)}"
        return plan
    location = await session.get(Location, line.location_id)
    if location is None or location.archived_at is not None:
        plan.conflict = "This location has been archived since the stock take started."
        return plan

    delta = counted - expected
    placed = placements._placed(rows)
    if delta > 0:
        absorbed = min(delta, max(item.total - placed, ZERO))
        plan.total_change = delta - absorbed
    else:
        plan.total_change = delta

    allocated = ZERO
    if ref.material_id is not None:
        material = await session.get(Material, ref.material_id)
        allocated = Decimal(material.allocated_qty or 0) if material else ZERO
    else:
        owner = (
            await session.get(ProductVariant, ref.variant_id)
            if ref.variant_id is not None
            else await session.get(Product, ref.product_id)
        )
        allocated = Decimal(owner.allocated_qty or 0) if owner else ZERO
        if allocated > 0 and delta < 0:
            units = "unit is" if allocated == 1 else "units are"
            plan.conflict = (
                f"{_qty(allocated)} {units} allocated to open orders and may be picked and boxed rather than "
                f"on the shelf: counted {_qty(counted)} against {_qty(expected)} expected"
            )
            return plan
    if item.total + plan.total_change < allocated:
        plan.conflict = f"Cannot reduce stock below {_qty(allocated)} already allocated to orders"
        return plan

    plan.full_count = here is not None and len(rows) == 1 and placed == item.total
    return plan


async def _apply_line(session: AsyncSession, line: StockTakeLine, plan: _Plan, take_id: int) -> None:
    ref = _ref(line)
    counted = Decimal(line.counted_qty)
    expected = Decimal(line.expected_qty)
    delta = counted - expected
    nodes = await locs.load_nodes(session)
    reason = f"Stock take #{take_id} at {locs.display_path(nodes, line.location_id)}"

    # The placement first, uncommitted: the adjustment below commits the session, so both land in
    # one transaction and a refused adjustment takes the placement change back with it.
    if delta != 0:
        was_primary = plan.here.is_primary if plan.here is not None else False
        if counted > 0:
            if plan.here is not None:
                plan.here.qty = counted
            else:
                session.add(
                    StockPlacement(
                        location_id=line.location_id, qty=counted, is_primary=not plan.rows, **ref.event_fields()
                    )
                )
        elif plan.here is not None:
            await session.delete(plan.here)
        session.add(
            PlacementEvent(
                **ref.event_fields(),
                from_location_id=line.location_id if delta < 0 else None,
                to_location_id=line.location_id if delta > 0 else None,
                qty=abs(delta),
                kind=PlacementEventKind.count,
                reason=f"{reason}: {_qty(expected)} → {_qty(counted)}",
            )
        )
        await session.flush()
        if was_primary and counted == 0:
            await placements._ensure_primary(session, ref)

    with following_paused():
        if plan.full_count:
            if ref.material_id is not None:
                await create_adjustment(session, ref.material_id, MaterialAdjustmentMode.set, counted, reason)
            else:
                await create_stock_adjustment(
                    session, ref.product_id, ref.variant_id, StockAdjustmentMode.set, int(counted), reason
                )
        elif plan.total_change != 0:
            if ref.material_id is not None:
                await create_adjustment(session, ref.material_id, MaterialAdjustmentMode.adjust, plan.total_change, reason)
            else:
                await create_stock_adjustment(
                    session, ref.product_id, ref.variant_id, StockAdjustmentMode.adjust, int(plan.total_change), reason
                )


async def apply_location_line(session: AsyncSession, line: StockTakeLine, take_id: int) -> str | None:
    """Plans and applies one counted line. Returns why it was held back, or None once applied."""
    plan = await _plan_line(session, line)
    if plan.conflict is not None:
        return plan.conflict
    await _apply_line(session, line, plan, take_id)
    return None


async def rebaseline(session: AsyncSession, line: StockTakeLine) -> None:
    """Makes "expected" what the place holds right now, for "use my count" on a line that moved."""
    here = next((r for r in await placements.item_placements(session, _ref(line)) if r.location_id == line.location_id), None)
    line.expected_qty = Decimal(here.qty or 0) if here is not None else ZERO


async def add_found(
    session: AsyncSession, take: StockTake, location_id: int, ref: ItemRef, counted: Decimal | None, notes: str | None
) -> StockTakeLine:
    """Puts an item on the sheet that turned up where it wasn't expected. If it already has a line
    for that place the count is simply entered on it."""
    if take.location_id is None:
        raise _bad("Only a take scoped by location can have items added to it.")
    nodes = await locs.load_nodes(session)
    if location_id not in nodes or (
        location_id != take.location_id and location_id not in locs.descendant_ids(nodes, take.location_id)
    ):
        raise _bad("That place isn't inside the location being counted.")
    if nodes[location_id].archived:
        raise _bad("That location is archived. Unarchive it first, or choose another.")
    await placements.resolve_item(session, ref)

    lines = (
        await session.scalars(
            select(StockTakeLine).where(StockTakeLine.stock_take_id == take.id, StockTakeLine.location_id == location_id)
        )
    ).all()
    existing = next(
        (l for l in lines if (l.material_id, l.product_id, l.variant_id) == (ref.material_id, ref.product_id, ref.variant_id)),
        None,
    )
    if existing is not None:
        return existing
    here = next((r for r in await placements.item_placements(session, ref) if r.location_id == location_id), None)
    line = StockTakeLine(
        stock_take_id=take.id,
        location_id=location_id,
        location_label=locs.display_path(nodes, location_id),
        expected_qty=Decimal(here.qty or 0) if here is not None else ZERO,
        found=True,
        status=StockTakeLineStatus.pending,
        **ref.event_fields(),
    )
    session.add(line)
    await session.flush()
    return line
