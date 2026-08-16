"""The stock take lifecycle: scope it, count it, approve it, resolve what's left.

Three things here are load-bearing and easy to undo by accident.

**expected_qty is a snapshot.** It records what the system believed when the take started
and is never refreshed. Comparing it against the live quantity at approval is the only way
to tell "the count was wrong" from "something moved while you were counting", and those
want opposite treatment — the first is a variance to apply, the second is two different
truths that need a human.

**A blank count is not a count of zero.** counted_qty NULL means the line was never
reached: nothing is adjusted and the item's counting clock is deliberately left alone,
because dating it would claim it had been verified.

**Allocated stock is physically absent.** Units picked for an open order are boxed and off
the shelf but still counted in current_stock until the order ships. A shelf count therefore
comes up short by exactly that much, and applying it would write off real stock that is
sitting by the door. Any short count on a line with allocations goes to manual review — not
just one below the allocated floor, which is the only case the adjustment service itself
would refuse. See approve_stock_take.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.material import Material, MaterialAdjustmentMode
from app.models.product import Product
from app.models.product_type import ProductType
from app.models.stock_adjustment import StockAdjustmentMode
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus
from app.models.variant import ProductVariant
from app.schemas.stock_take import ScopePreview, ScopeWarning, StockTakeScope
from app.services import abc
from app.services.costing import create_adjustment
from app.services.stock_adjustments import create_stock_adjustment

_REASON = "Stock take #{take_id}"


@dataclass(frozen=True)
class _Candidate:
    """One row that holds stock and could be counted."""

    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    unit: str
    expected_qty: Decimal
    allocated_qty: Decimal | None

    @property
    def key(self) -> tuple:
        return (self.material_id, self.product_id, self.variant_id)


async def _material_candidates(session: AsyncSession, scope: StockTakeScope, due_keys: set | None) -> list[_Candidate]:
    query = select(Material).where(Material.is_active.is_(True)).order_by(Material.name)
    if scope.material_categories:
        query = query.where(Material.category.in_(scope.material_categories))
    out = []
    for m in (await session.execute(query)).scalars():
        if due_keys is not None and (m.id, None, None) not in due_keys:
            continue
        out.append(
            _Candidate(
                material_id=m.id,
                product_id=None,
                variant_id=None,
                name=m.name,
                unit=m.unit.value,
                expected_qty=Decimal(m.current_qty),
                allocated_qty=None,
            )
        )
    return out


async def _product_candidates(session: AsyncSession, scope: StockTakeScope, due_keys: set | None) -> list[_Candidate]:
    """Products contribute their active variants when they have any, themselves otherwise.

    Bundles contribute nothing: their quantity is derived from their components, so there
    is no row to count and nothing an adjustment could write to.
    """
    query = (
        select(Product)
        .where(Product.is_active.is_(True), Product.is_bundle.is_(False))
        .order_by(Product.name)
    )
    if scope.product_type_ids:
        query = query.where(Product.product_type_id.in_(scope.product_type_ids))
    products = list((await session.execute(query)).scalars())
    if not products:
        return []

    variants_by_product: dict[int, list[ProductVariant]] = {}
    rows = (
        await session.execute(
            select(ProductVariant).where(
                ProductVariant.is_active.is_(True),
                ProductVariant.product_id.in_([p.id for p in products]),
            )
        )
    ).scalars()
    for v in rows:
        variants_by_product.setdefault(v.product_id, []).append(v)

    out = []
    for p in products:
        variants = sorted(variants_by_product.get(p.id, []), key=lambda v: v.variant_name)
        owners = (
            [(v.id, f"{p.name} — {v.variant_name}", v.current_stock, v.allocated_qty) for v in variants]
            if variants
            else [(None, p.name, p.current_stock, p.allocated_qty)]
        )
        for variant_id, name, current_stock, allocated in owners:
            if due_keys is not None and (None, p.id, variant_id) not in due_keys:
                continue
            out.append(
                _Candidate(
                    material_id=None,
                    product_id=p.id,
                    variant_id=variant_id,
                    name=name,
                    unit="each",
                    expected_qty=Decimal(current_stock),
                    allocated_qty=Decimal(allocated),
                )
            )
    return out


async def resolve_candidates(session: AsyncSession, scope: StockTakeScope) -> list[_Candidate]:
    if not scope.include_materials and not scope.include_products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose materials, finished stock, or both",
        )
    # Resolved once up front rather than per item: compute_due_for_count already loads the
    # classification rules in one go, and asking per candidate would undo that.
    due_keys = None
    if scope.overdue_only:
        due_keys = {
            (d.material_id, d.product_id, d.variant_id) for d in await abc.compute_due_for_count(session)
        }

    candidates: list[_Candidate] = []
    if scope.include_materials:
        candidates += await _material_candidates(session, scope, due_keys)
    if scope.include_products:
        candidates += await _product_candidates(session, scope, due_keys)
    return candidates


async def describe_scope(session: AsyncSession, scope: StockTakeScope) -> str:
    """A sentence for the log, rendered now rather than recomputed later.

    The inputs behind it can be renamed, merged or deleted afterwards; what was counted
    at the time can't change.
    """
    parts = []
    if scope.include_materials:
        if scope.material_categories:
            parts.append("materials in " + ", ".join(sorted(c.value for c in scope.material_categories)))
        else:
            parts.append("all materials")
    if scope.include_products:
        if scope.product_type_ids:
            names = (
                await session.execute(select(ProductType.name).where(ProductType.id.in_(scope.product_type_ids)))
            ).scalars()
            listed = ", ".join(sorted(names))
            parts.append(f"products of type {listed}" if listed else "products of a since-deleted type")
        else:
            parts.append("all finished stock")
    description = " and ".join(parts) if parts else "nothing"
    if scope.overdue_only:
        description += ", due for counting only"
    return description[0].upper() + description[1:]


async def _open_take_warnings(session: AsyncSession, candidates: list[_Candidate]) -> list[ScopeWarning]:
    """Which candidates are already on another open take.

    A soft lock: reported so the user knows, never enforced. Counting one item on two takes
    is odd but legitimate, and blocking it would strand someone behind a take they forgot
    to close.
    """
    if not candidates:
        return []
    rows = (
        await session.execute(
            select(StockTakeLine, StockTake)
            .join(StockTake, StockTakeLine.stock_take_id == StockTake.id)
            .where(StockTake.status == StockTakeStatus.open)
        )
    ).all()
    by_key = {c.key: c for c in candidates}
    warnings = []
    for line, take in rows:
        candidate = by_key.get((line.material_id, line.product_id, line.variant_id))
        if candidate is not None:
            warnings.append(
                ScopeWarning(
                    name=candidate.name,
                    other_stock_take_id=take.id,
                    other_started_at=take.started_at,
                )
            )
    return warnings


async def preview_scope(session: AsyncSession, scope: StockTakeScope) -> ScopePreview:
    candidates = await resolve_candidates(session, scope)
    return ScopePreview(
        candidate_count=len(candidates),
        material_count=sum(1 for c in candidates if c.material_id is not None),
        product_count=sum(1 for c in candidates if c.product_id is not None),
        scope_description=await describe_scope(session, scope),
        warnings=await _open_take_warnings(session, candidates),
    )


async def create_stock_take(session: AsyncSession, scope: StockTakeScope) -> tuple[StockTake, list[ScopeWarning]]:
    candidates = await resolve_candidates(session, scope)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nothing to count — no items match that scope",
        )
    warnings = await _open_take_warnings(session, candidates)

    take = StockTake(
        status=StockTakeStatus.open,
        includes_materials=scope.include_materials,
        includes_products=scope.include_products,
        overdue_only=scope.overdue_only,
        scope_description=await describe_scope(session, scope),
    )
    session.add(take)
    await session.flush()

    for c in candidates:
        session.add(
            StockTakeLine(
                stock_take_id=take.id,
                material_id=c.material_id,
                product_id=c.product_id,
                variant_id=c.variant_id,
                expected_qty=c.expected_qty,
                allocated_qty_at_start=c.allocated_qty,
                status=StockTakeLineStatus.pending,
            )
        )
    await session.commit()
    await session.refresh(take)
    return take, warnings


async def get_take_or_404(session: AsyncSession, stock_take_id: int) -> StockTake:
    take = await session.get(StockTake, stock_take_id)
    if take is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock take not found")
    return take


async def _get_line_or_404(session: AsyncSession, stock_take_id: int, line_id: int) -> StockTakeLine:
    line = await session.get(StockTakeLine, line_id)
    if line is None or line.stock_take_id != stock_take_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock take line not found")
    return line


def _apply_count(line: StockTakeLine, counted_qty: Decimal | None, notes: str | None) -> None:
    line.counted_qty = counted_qty
    line.notes = notes
    # Only the two live states are touched here. A line already applied or resolved keeps
    # its outcome — re-entering a count on a finished line is a resolve, not an edit.
    if line.status in (StockTakeLineStatus.pending, StockTakeLineStatus.counted):
        line.status = StockTakeLineStatus.counted if counted_qty is not None else StockTakeLineStatus.pending


async def set_line_count(
    session: AsyncSession, stock_take_id: int, line_id: int, counted_qty: Decimal | None, notes: str | None
) -> StockTakeLine:
    take = await get_take_or_404(session, stock_take_id)
    if take.status is StockTakeStatus.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This stock take is closed")
    line = await _get_line_or_404(session, stock_take_id, line_id)
    await _validate_count(session, line, counted_qty)
    _apply_count(line, counted_qty, notes)
    await session.commit()
    await session.refresh(line)
    return line


async def set_line_counts(session: AsyncSession, stock_take_id: int, entries) -> int:
    """Bulk count entry — what the count sheet's Save and the confirmed CSV import both use."""
    take = await get_take_or_404(session, stock_take_id)
    if take.status is StockTakeStatus.closed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This stock take is closed")
    lines = {
        line.id: line
        for line in (
            await session.execute(select(StockTakeLine).where(StockTakeLine.stock_take_id == stock_take_id))
        ).scalars()
    }
    changed = 0
    for entry in entries:
        line = lines.get(entry.line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Line {entry.line_id} is not on this stock take"
            )
        await _validate_count(session, line, entry.counted_qty)
        _apply_count(line, entry.counted_qty, entry.notes)
        changed += 1
    await session.commit()
    return changed


async def _validate_count(session: AsyncSession, line: StockTakeLine, counted_qty: Decimal | None) -> None:
    """Reject a count the units can't express, before it reaches the sheet.

    Reuses validate_qty_for_unit so a material measured in `each` refuses 2.5 here for the
    same reason and with the same message it would anywhere else. Products are always whole
    units.
    """
    if counted_qty is None:
        return
    if line.material_id is not None:
        from app.services.validation import validate_qty_for_unit

        material = await session.get(Material, line.material_id)
        if material is not None:
            validate_qty_for_unit(counted_qty, material.unit, "counted_qty")
    elif counted_qty != counted_qty.to_integral_value():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Counted quantity must be a whole number for finished stock",
        )


def _qty(value: Decimal) -> str:
    """A quantity as someone would write it: 10 rather than 10.0000, 2.5 rather than 2.5000.

    Decimal keeps the scale it was stored with, and format spec "g" preserves it too, so
    these reasons otherwise read "counted 5.0000 against 10.0000 expected" in a sentence
    meant for a person.
    """
    normalized = value.normalize()
    # normalize() turns 10 into 1E+1, which is worse than what it fixed.
    return f"{normalized:f}" if normalized == normalized.to_integral_value() else str(normalized)


@dataclass
class _LineOutcome:
    status: StockTakeLineStatus
    conflict_reason: str | None = None
    system_qty: Decimal | None = None


async def _current_qty_and_allocated(
    session: AsyncSession, line: StockTakeLine
) -> tuple[Decimal, Decimal, object]:
    if line.material_id is not None:
        material = await session.get(Material, line.material_id)
        if material is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material no longer exists")
        return Decimal(material.current_qty), Decimal(0), material
    owner = (
        await session.get(ProductVariant, line.variant_id)
        if line.variant_id is not None
        else await session.get(Product, line.product_id)
    )
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product no longer exists")
    return Decimal(owner.current_stock), Decimal(owner.allocated_qty), owner


def _decide(line: StockTakeLine, current_qty: Decimal, allocated: Decimal) -> _LineOutcome:
    """What should happen to this line, decided before anything is written.

    Deciding in Python first is not only clearer — it is what keeps the adjustment service
    from ever refusing. A refusal rolls back inside the service and leaves the session
    unusable (docs/backlog.md), so the cheapest defence is to not provoke one.
    """
    if line.counted_qty is None:
        return _LineOutcome(StockTakeLineStatus.skipped)

    counted = Decimal(line.counted_qty)
    expected = Decimal(line.expected_qty)

    # Movement first: it is the more specific fact when a line both moved and has
    # allocations, and it is the one the reviewer can act on.
    if current_qty != expected:
        return _LineOutcome(
            StockTakeLineStatus.conflict,
            f"Stock moved since this take started — was {_qty(expected)}, now {_qty(current_qty)}",
            current_qty,
        )

    # The case that would otherwise apply cleanly and destroy stock: counting only the
    # loose units on a line with allocations lands at or above the floor the adjustment
    # service checks, so nothing refuses it and the boxed units are written off.
    if allocated > 0 and counted < expected:
        units = "unit is" if allocated == 1 else "units are"
        return _LineOutcome(
            StockTakeLineStatus.conflict,
            f"{_qty(allocated)} {units} allocated to open orders and may be picked and boxed "
            f"rather than on the shelf — counted {_qty(counted)} against {_qty(expected)} expected",
            current_qty,
        )

    return _LineOutcome(StockTakeLineStatus.applied)


async def _apply_line(session: AsyncSession, line: StockTakeLine, take_id: int) -> None:
    """Commit one counted line through the existing set-adjustment services.

    mode=set because that is what a physical count is, and it carries the counted total
    into target_qty for the history. A count confirming the existing quantity still writes
    a zero-delta row — the adjustment models allow that case deliberately — so the stock
    history says "counted and confirmed on this date" rather than leaving a silent gap.

    Phase A.1 already made a set adjustment stamp last_stock_take_at, so that happens
    inside these calls; only last_stock_take_id is left to attribute it to this take.
    """
    counted = Decimal(line.counted_qty)
    reason = _REASON.format(take_id=take_id)
    if line.material_id is not None:
        material = await create_adjustment(session, line.material_id, MaterialAdjustmentMode.set, counted, reason)
        # create_adjustment returns the material, not the adjustment it wrote, so the row
        # is found by being the newest for this material — safe because it has just
        # committed inside its own session and nothing else writes here concurrently.
        line.material_adjustment_id = await _latest_material_adjustment_id(session, line.material_id)
        material.last_stock_take_id = take_id
    else:
        adjustment = await create_stock_adjustment(
            session, line.product_id, line.variant_id, StockAdjustmentMode.set, int(counted), reason
        )
        line.stock_adjustment_id = adjustment.id
        owner = (
            await session.get(ProductVariant, line.variant_id)
            if line.variant_id is not None
            else await session.get(Product, line.product_id)
        )
        owner.last_stock_take_id = take_id


async def _latest_material_adjustment_id(session: AsyncSession, material_id: int) -> int | None:
    from app.models.material import MaterialAdjustment

    return (
        await session.execute(
            select(MaterialAdjustment.id)
            .where(MaterialAdjustment.material_id == material_id)
            .order_by(MaterialAdjustment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class ApproveCounts:
    """What approve did. The router pairs this with a freshly-read take for the response —
    the service deliberately doesn't build the read shape, since it has been writing
    through other sessions and its own copy of the take is stale by definition."""

    applied: int
    conflicts: int
    skipped: int


async def approve_stock_take(session: AsyncSession, stock_take_id: int) -> ApproveCounts:
    """Apply every counted line that can be applied, flag the rest, and close the take.

    **One fresh session per line.** Both adjustment services commit internally, and a
    refusal inside one rolls back and leaves that session permanently unusable — every
    later statement on it raises MissingGreenlet, including a bare SELECT, and rolling back
    from outside does not recover it (docs/backlog.md). A single-session loop that caught a
    refusal would therefore die on the *next* line rather than the guilty one. Giving each
    line its own short session from async_session_factory means a poisoned session is
    discarded with its own iteration and cannot reach anything else. Sessions never overlap,
    so SQLite's single-writer rule is satisfied too.

    Idempotent: lines already applied or resolved are skipped, so an approval interrupted
    part-way is fixed by running it again.
    """
    take = await get_take_or_404(session, stock_take_id)
    line_ids = list(
        (
            await session.execute(
                select(StockTakeLine.id)
                .where(StockTakeLine.stock_take_id == stock_take_id)
                .order_by(StockTakeLine.id)
            )
        ).scalars()
    )
    was_closed = take.status is StockTakeStatus.closed

    applied = conflicts = skipped = 0
    for line_id in line_ids:
        outcome = await _approve_one_line(line_id, stock_take_id)
        if outcome is StockTakeLineStatus.applied:
            applied += 1
        elif outcome is StockTakeLineStatus.conflict:
            conflicts += 1
        elif outcome is StockTakeLineStatus.skipped:
            skipped += 1

    if not was_closed:
        # Closing is unconditional: unresolved lines carry forward as standing variances
        # rather than holding the take open. They stay visible in their own view.
        take.status = StockTakeStatus.closed
        take.closed_at = datetime.now(timezone.utc)
        await session.commit()

    return ApproveCounts(applied=applied, conflicts=conflicts, skipped=skipped)


async def _approve_one_line(line_id: int, take_id: int) -> StockTakeLineStatus | None:
    """Decide and apply a single line inside its own session. Returns what it became."""
    async with async_session_factory() as line_session:
        line = await line_session.get(StockTakeLine, line_id)
        if line is None:
            return None
        # Already finished — idempotency, so re-running approve is safe.
        if line.status in (
            StockTakeLineStatus.applied,
            StockTakeLineStatus.accepted_system,
            StockTakeLineStatus.conflict,
            StockTakeLineStatus.skipped,
        ):
            return None

        current_qty, allocated, _owner = await _current_qty_and_allocated(line_session, line)
        outcome = _decide(line, current_qty, allocated)

        if outcome.status is not StockTakeLineStatus.applied:
            line.status = outcome.status
            line.conflict_reason = outcome.conflict_reason
            line.system_qty_at_approval = outcome.system_qty
            await line_session.commit()
            return outcome.status

        # Status written before the service call so the one commit inside it carries both.
        line.status = StockTakeLineStatus.applied
        try:
            await _apply_line(line_session, line, take_id)
            await line_session.commit()
            return StockTakeLineStatus.applied
        except HTTPException as exc:
            # Should be unreachable — _decide pre-checks everything the services refuse —
            # so getting here means something changed underneath us between check and
            # write. This session is now unusable; record the outcome from a clean one.
            await _record_conflict_in_new_session(line_id, str(exc.detail), current_qty)
            return StockTakeLineStatus.conflict


async def _record_conflict_in_new_session(line_id: int, reason: str, system_qty: Decimal) -> None:
    async with async_session_factory() as session:
        line = await session.get(StockTakeLine, line_id)
        if line is None:
            return
        line.status = StockTakeLineStatus.conflict
        line.conflict_reason = reason
        line.system_qty_at_approval = system_qty
        await session.commit()


async def resolve_line(
    session: AsyncSession, stock_take_id: int, line_id: int, action: str
) -> StockTakeLine:
    """Settle a flagged line. Works whether its take is still open or long closed.

    `reset` differs by state on purpose: on an open take the line goes back to pending and
    can be re-entered, but a closed take has no sheet to re-enter it in, so it becomes
    skipped — the item keeps its old count date and stays due, which is exactly "leave it,
    I'll catch it next time".
    """
    take = await get_take_or_404(session, stock_take_id)
    line = await _get_line_or_404(session, stock_take_id, line_id)

    if action == "accept_system":
        line.counted_qty = None
        line.status = StockTakeLineStatus.accepted_system
        line.resolved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(line)
        return line

    if action == "reset":
        line.counted_qty = None
        line.conflict_reason = None
        line.system_qty_at_approval = None
        line.status = (
            StockTakeLineStatus.pending
            if take.status is StockTakeStatus.open
            else StockTakeLineStatus.skipped
        )
        line.resolved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(line)
        return line

    if action != "accept_counted":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown action '{action}'")

    if line.counted_qty is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There is no counted value to accept — re-count this line instead",
        )

    # Its own session for the same reason approve uses one per line: this call can refuse
    # (the allocated floor is re-checked live and may have moved again), and a refusal
    # would poison whichever session it ran in.
    detail = await _accept_counted_in_new_session(line_id, stock_take_id)
    if detail is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    await session.refresh(line)
    return line


async def _accept_counted_in_new_session(line_id: int, take_id: int) -> str | None:
    """Apply a flagged line's counted value. Returns an error message, or None on success."""
    async with async_session_factory() as line_session:
        line = await line_session.get(StockTakeLine, line_id)
        if line is None:
            return "Stock take line not found"
        line.status = StockTakeLineStatus.applied
        line.conflict_reason = None
        line.resolved_at = datetime.now(timezone.utc)
        try:
            await _apply_line(line_session, line, take_id)
            await line_session.commit()
            return None
        except HTTPException as exc:
            return str(exc.detail)


async def unresolved_variances(session: AsyncSession) -> list[tuple[StockTakeLine, StockTake]]:
    """Flagged lines whose take has closed — variances still standing.

    Scoped to closed takes because a flagged line on an open take is just part of that
    take's review; it only becomes a standing variance once the take is done with.
    """
    return list(
        (
            await session.execute(
                select(StockTakeLine, StockTake)
                .join(StockTake, StockTakeLine.stock_take_id == StockTake.id)
                .where(
                    StockTakeLine.status == StockTakeLineStatus.conflict,
                    StockTake.status == StockTakeStatus.closed,
                )
                .order_by(StockTakeLine.id)
            )
        ).all()
    )


async def delete_stock_take(session: AsyncSession, stock_take_id: int) -> None:
    """Abandon an open take. Closed ones are a record and stay put."""
    take = await get_take_or_404(session, stock_take_id)
    if take.status is StockTakeStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A closed stock take is a record of what happened and can't be deleted",
        )
    await session.delete(take)
    await session.commit()


def line_display_name(line: StockTakeLine, materials: dict, products: dict, variants: dict) -> tuple[str, str]:
    """(name, unit) for a line, from pre-built lookups so a list doesn't go N+1."""
    if line.material_id is not None:
        material = materials.get(line.material_id)
        return (material.name if material else "(deleted material)", material.unit.value if material else "")
    product = products.get(line.product_id)
    base = product.name if product else "(deleted product)"
    if line.variant_id is not None:
        variant = variants.get(line.variant_id)
        return (f"{base} — {variant.variant_name}" if variant else f"{base} — (deleted variant)", "each")
    return (base, "each")


__all__ = [
    "ApproveCounts",
    "approve_stock_take",
    "create_stock_take",
    "delete_stock_take",
    "get_take_or_404",
    "line_display_name",
    "preview_scope",
    "resolve_line",
    "set_line_count",
    "set_line_counts",
    "unresolved_variances",
]
