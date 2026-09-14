"""Stock takes.

Route ordering matters here: FastAPI matches in declaration order, so every literal
segment (`/overdue`, `/preview-scope`, `/unresolved-variances`) must be declared above
`/{stock_take_id}` or it gets swallowed as an id. routers/materials.py has the same hazard
and solves it the same way.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.material import Material
from app.models.product import Product
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus
from app.models.variant import ProductVariant
from app.schemas.abc import DueForCountItemRead
from app.schemas.stock_take import (
    ApproveResult,
    BulkLineCountRequest,
    LineCountUpdate,
    LineResolution,
    ScopePreview,
    StockTakeProgress,
    StockTakeCreated,
    StockTakeDetail,
    StockTakeImportResult,
    StockTakeLineRead,
    StockTakeRead,
    StockTakeScope,
    UnresolvedVariance,
)
from app.services import abc, stock_takes
from app.services.csv_io import export_stock_take_csv, import_stock_take_csv

router = APIRouter(prefix="/stock-takes", tags=["stock-takes"], dependencies=[Depends(require_auth)])


async def _name_lookups(session: AsyncSession, lines: list[StockTakeLine]) -> tuple[dict, dict, dict]:
    """Batch the owner rows a set of lines refers to, so rendering names isn't N+1."""
    material_ids = {line.material_id for line in lines if line.material_id}
    product_ids = {line.product_id for line in lines if line.product_id}
    variant_ids = {line.variant_id for line in lines if line.variant_id}
    materials = (
        {
            m.id: m
            # group_lines reads material_type_name for the sub-group heading; without eager
            # loading that's a lazy load mid-iteration, which async SQLAlchemy can't do
            # (MissingGreenlet). category_ref is already lazy="selectin" on the model.
            for m in (
                await session.execute(
                    select(Material)
                    .options(selectinload(Material.material_type))
                    .where(Material.id.in_(material_ids))
                )
            ).scalars()
        }
        if material_ids
        else {}
    )
    products = (
        {
            p.id: p
            # group_lines reads product_category_name for the group heading; Product's
            # product_category relationship has no lazy="selectin", so without this it's a
            # lazy load mid-iteration that async SQLAlchemy can't do (MissingGreenlet) —
            # the same hazard the materials query above guards against.
            for p in (
                await session.execute(
                    select(Product)
                    .options(selectinload(Product.product_category))
                    .where(Product.id.in_(product_ids))
                )
            ).scalars()
        }
        if product_ids
        else {}
    )
    variants = (
        {
            v.id: v
            for v in (
                await session.execute(select(ProductVariant).where(ProductVariant.id.in_(variant_ids)))
            ).scalars()
        }
        if variant_ids
        else {}
    )
    return materials, products, variants


def _to_line_read(grouped: stock_takes.GroupedLine) -> StockTakeLineRead:
    line = grouped.line
    # Derived rather than stored so it can never disagree with the two numbers it comes
    # from; None when nothing was counted, which is not the same as a delta of zero.
    delta = None if line.counted_qty is None else Decimal(line.counted_qty) - Decimal(line.expected_qty)
    return StockTakeLineRead(
        id=line.id,
        material_id=line.material_id,
        product_id=line.product_id,
        variant_id=line.variant_id,
        name=grouped.name,
        unit=grouped.unit,
        section=grouped.section,
        group=grouped.group,
        subgroup=grouped.subgroup,
        expected_qty=Decimal(line.expected_qty),
        allocated_qty_at_start=(
            None if line.allocated_qty_at_start is None else Decimal(line.allocated_qty_at_start)
        ),
        counted_qty=None if line.counted_qty is None else Decimal(line.counted_qty),
        notes=line.notes,
        status=line.status,
        system_qty_at_approval=(
            None if line.system_qty_at_approval is None else Decimal(line.system_qty_at_approval)
        ),
        conflict_reason=line.conflict_reason,
        delta=delta,
    )


def _open_days(take: StockTake) -> int:
    started = take.started_at
    if started is None:
        return 0
    # SQLite doesn't reliably round-trip tzinfo (see services/platforms/base.ensure_utc),
    # so reattach it rather than comparing a naive value against an aware one.
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    end = take.closed_at or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max((end - started).days, 0)


# A line "had a count completed" if a number was entered on the sheet and the line was
# carried forward with it — whether it applied cleanly or got flagged for review. `skipped`
# (left blank) and `pending`/`counted`-but-nothing means it did not.
_COUNT_COMPLETED = frozenset(
    {StockTakeLineStatus.counted, StockTakeLineStatus.applied, StockTakeLineStatus.conflict}
)


def _progress_status(take: StockTake, lines: list[StockTakeLine]) -> StockTakeProgress:
    if take.status is not StockTakeStatus.closed:
        return StockTakeProgress.open
    applied = sum(1 for line in lines if line.status is StockTakeLineStatus.applied)
    if lines and applied == len(lines):
        return StockTakeProgress.completed
    if applied > 0:
        return StockTakeProgress.partially_completed
    return StockTakeProgress.closed


def _take_fields(take: StockTake, lines: list[StockTakeLine]) -> dict:
    """The stored columns plus the counts derived from this take's lines.

    Built explicitly rather than via model_validate: open_days and the four counts have no
    column behind them, so validating the ORM object would just report them missing. Giving
    them schema defaults instead would let a caller that forgot to pass them silently
    return zeroes.
    """
    return {
        "id": take.id,
        "status": take.status,
        "includes_materials": take.includes_materials,
        "includes_products": take.includes_products,
        "overdue_only": take.overdue_only,
        "scope_description": take.scope_description,
        "started_at": take.started_at,
        "closed_at": take.closed_at,
        "notes": take.notes,
        "open_days": _open_days(take),
        "progress_status": _progress_status(take, lines),
        "line_count": len(lines),
        "counted_count": sum(1 for line in lines if line.status is StockTakeLineStatus.counted),
        "completed_count": sum(1 for line in lines if line.status in _COUNT_COMPLETED),
        "pending_count": sum(1 for line in lines if line.status is StockTakeLineStatus.pending),
        "conflict_count": sum(1 for line in lines if line.status is StockTakeLineStatus.conflict),
    }


async def _read_take(session: AsyncSession, take: StockTake) -> StockTakeDetail:
    lines = list(
        (
            await session.execute(
                select(StockTakeLine)
                .where(StockTakeLine.stock_take_id == take.id)
                .order_by(StockTakeLine.id)
            )
        ).scalars()
    )
    materials, products, variants = await _name_lookups(session, lines)
    # Arranged the way the stock is arranged, not the order the rows happen to have been
    # created in. See services/stock_takes.group_lines — the CSV and the variances list go
    # through the same call, which is the only reason all three agree.
    grouped = stock_takes.group_lines(lines, materials, products, variants)
    return StockTakeDetail(
        **_take_fields(take, lines),
        lines=[_to_line_read(g) for g in grouped],
    )


# --- literal segments, declared before /{stock_take_id} ---------------------------------


@router.get("", response_model=list[StockTakeRead])
async def list_stock_takes(session: AsyncSession = Depends(get_db)) -> list[StockTakeRead]:
    takes = list(
        (await session.execute(select(StockTake).order_by(StockTake.started_at.desc()))).scalars()
    )
    if not takes:
        return []
    all_lines = list(
        (
            await session.execute(
                select(StockTakeLine).where(StockTakeLine.stock_take_id.in_([t.id for t in takes]))
            )
        ).scalars()
    )
    by_take: dict[int, list[StockTakeLine]] = {}
    for line in all_lines:
        by_take.setdefault(line.stock_take_id, []).append(line)
    return [StockTakeRead(**_take_fields(t, by_take.get(t.id, []))) for t in takes]


@router.get("/overdue", response_model=list[DueForCountItemRead])
async def list_overdue(session: AsyncSession = Depends(get_db)) -> list:
    """Everything whose cadence says it wants counting, most overdue first.

    Never-counted items lead the list with a null days_overdue rather than a fabricated
    one — on a database that has never had a stock take, that is every item, which is the
    honest starting state rather than a bug.
    """
    return await abc.compute_due_for_count(session)


@router.get("/unresolved-variances", response_model=list[UnresolvedVariance])
async def list_unresolved_variances(session: AsyncSession = Depends(get_db)) -> list[UnresolvedVariance]:
    """Flagged lines whose take has closed, across every take.

    A separate view rather than a filter on a take's detail page: the point is that
    following one up shouldn't depend on remembering which take it came from.
    """
    rows = await stock_takes.unresolved_variances(session)
    lines = [line for line, _ in rows]
    materials, products, variants = await _name_lookups(session, lines)
    # Same arrangement as the count sheet, through the same call. These are read while
    # walking the same shelves, so they group the same way.
    take_by_line = {line.id: take for line, take in rows}
    return [
        UnresolvedVariance(
            line=_to_line_read(g),
            stock_take_id=take_by_line[g.line.id].id,
            stock_take_closed_at=take_by_line[g.line.id].closed_at,
        )
        for g in stock_takes.group_lines(lines, materials, products, variants)
    ]


@router.post("/preview-scope", response_model=ScopePreview)
async def preview_scope(scope: StockTakeScope, session: AsyncSession = Depends(get_db)) -> ScopePreview:
    """What a take with this scope would contain. Writes nothing."""
    return await stock_takes.preview_scope(session, scope)


@router.post("", response_model=StockTakeCreated, status_code=status.HTTP_201_CREATED)
async def create_stock_take(scope: StockTakeScope, session: AsyncSession = Depends(get_db)) -> StockTakeCreated:
    take, warnings = await stock_takes.create_stock_take(session, scope)
    return StockTakeCreated(stock_take=await _read_take(session, take), warnings=warnings)


# --- /{stock_take_id} and below ---------------------------------------------------------


@router.get("/{stock_take_id}", response_model=StockTakeDetail)
async def get_stock_take(stock_take_id: int, session: AsyncSession = Depends(get_db)) -> StockTakeDetail:
    return await _read_take(session, await stock_takes.get_take_or_404(session, stock_take_id))


@router.delete("/{stock_take_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stock_take(stock_take_id: int, session: AsyncSession = Depends(get_db)) -> None:
    """Abandon an open take. A closed one is a record of what happened and stays."""
    await stock_takes.delete_stock_take(session, stock_take_id)


@router.patch("/{stock_take_id}/lines/{line_id}", response_model=StockTakeDetail)
async def set_line_count(
    stock_take_id: int,
    line_id: int,
    payload: LineCountUpdate,
    session: AsyncSession = Depends(get_db),
) -> StockTakeDetail:
    await stock_takes.set_line_count(session, stock_take_id, line_id, payload.counted_qty, payload.notes)
    return await _read_take(session, await stock_takes.get_take_or_404(session, stock_take_id))


@router.put("/{stock_take_id}/lines", response_model=StockTakeDetail)
async def set_line_counts(
    stock_take_id: int,
    payload: BulkLineCountRequest,
    session: AsyncSession = Depends(get_db),
) -> StockTakeDetail:
    """Bulk count entry — the count sheet's Save, and what a confirmed CSV import calls."""
    await stock_takes.set_line_counts(session, stock_take_id, payload.lines)
    return await _read_take(session, await stock_takes.get_take_or_404(session, stock_take_id))


@router.post("/{stock_take_id}/lines/{line_id}/resolve", response_model=StockTakeDetail)
async def resolve_line(
    stock_take_id: int,
    line_id: int,
    payload: LineResolution,
    session: AsyncSession = Depends(get_db),
) -> StockTakeDetail:
    await stock_takes.resolve_line(session, stock_take_id, line_id, payload.action)
    return await _read_take(session, await stock_takes.get_take_or_404(session, stock_take_id))


@router.get("/{stock_take_id}/export")
async def export_stock_take(stock_take_id: int, session: AsyncSession = Depends(get_db)) -> Response:
    await stock_takes.get_take_or_404(session, stock_take_id)
    csv_text = await export_stock_take_csv(session, stock_take_id)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=stock-take-{stock_take_id}.csv"},
    )


@router.post("/{stock_take_id}/import", response_model=StockTakeImportResult)
async def import_stock_take(
    stock_take_id: int,
    file: UploadFile,
    dry_run: bool = True,
    on_error: str = "skip",
    session: AsyncSession = Depends(get_db),
) -> StockTakeImportResult:
    """Read counts off a filled-in sheet.

    Called twice by design: once with dry_run=true to produce the preview the user
    confirms, then again with the same file and dry_run=false. Nothing is written on the
    first call, so a file with problems can be reported without half-applying it.
    """
    if on_error not in ("skip", "fail"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="on_error must be 'skip' or 'fail'"
        )
    content = await file.read()
    result = await import_stock_take_csv(session, stock_take_id, content, dry_run=dry_run, on_error=on_error)
    return StockTakeImportResult(**result)


@router.post("/{stock_take_id}/approve", response_model=ApproveResult)
async def approve_stock_take(stock_take_id: int, session: AsyncSession = Depends(get_db)) -> ApproveResult:
    counts = await stock_takes.approve_stock_take(session, stock_take_id)
    # Re-read rather than reusing anything held before the call: approve writes through its
    # own per-line sessions, so this session's copies are stale by construction.
    session.expire_all()
    take = await stock_takes.get_take_or_404(session, stock_take_id)
    return ApproveResult(
        stock_take=await _read_take(session, take),
        applied_count=counts.applied,
        conflict_count=counts.conflicts,
        skipped_count=counts.skipped,
    )
