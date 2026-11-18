from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.material_unit import MaterialUnitRow
from app.schemas.material_unit import (
    MaterialUnitFindOrCreate,
    MaterialUnitRead,
    MaterialUnitReorder,
    MaterialUnitUpdate,
)
from app.services import units

router = APIRouter(prefix="/material-units", tags=["material-units"], dependencies=[Depends(require_auth)])

_ORDER = (MaterialUnitRow.sort_order, MaterialUnitRow.name, MaterialUnitRow.id)


async def _list_with_usage(session: AsyncSession) -> list[MaterialUnitRow]:
    rows = list((await session.execute(select(MaterialUnitRow).order_by(*_ORDER))).scalars())
    counts = await units.usage_counts(session)
    for row in rows:
        # On the ORM instance so from_attributes picks it up; the column doesn't exist.
        row.usage_count = counts.get(row.name, 0)
    return rows


@router.get("", response_model=list[MaterialUnitRead])
async def list_material_units(session: AsyncSession = Depends(get_db)) -> list[MaterialUnitRow]:
    return await _list_with_usage(session)


@router.post("/find-or-create", response_model=MaterialUnitRead)
async def find_or_create_material_unit(
    payload: MaterialUnitFindOrCreate, session: AsyncSession = Depends(get_db)
) -> MaterialUnitRow:
    """Case-insensitive, so adding "M" when "m" exists returns the existing unit rather than
    creating a second one nobody meant. A new unit starts with no precision limit and a visible
    suffix; both are edited on the row afterwards."""
    cleaned = payload.name.strip()
    if not cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")
    row = await units.find_by_name_ci(session, cleaned)
    if row is None:
        row = MaterialUnitRow(name=cleaned, sort_order=await units.next_sort_order(session))
        session.add(row)
    await session.commit()
    await session.refresh(row)
    row.usage_count = (await units.usage_counts(session)).get(row.name, 0)
    return row


@router.patch("/{row_id}", response_model=MaterialUnitRead)
async def update_material_unit(
    row_id: int, payload: MaterialUnitUpdate, session: AsyncSession = Depends(get_db)
) -> MaterialUnitRow:
    row = await session.get(MaterialUnitRow, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")

    old_name = row.name
    if name != old_name:
        clash = await units.find_by_name_ci(session, name)
        if clash is not None and clash.id != row.id:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=f'Another unit is already called "{name}".')
        row.name = name
        # Materials hold the name, so they have to follow it.
        await units.rename_references(session, old_name, name)

    for key, value in payload.model_dump(exclude={"name"}, exclude_unset=True).items():
        # sort_order and show_suffix are NOT NULL: an explicit null means "leave it".
        if value is None and key != "decimal_places":
            continue
        setattr(row, key, value)

    await session.commit()
    await session.refresh(row)
    row.usage_count = (await units.usage_counts(session)).get(row.name, 0)
    return row


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_unit(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    row = await session.get(MaterialUnitRow, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    # Every material needs a unit, so the last one can't go even when nothing uses it yet.
    remaining = (await session.execute(select(func.count()).select_from(MaterialUnitRow))).scalar_one()
    if remaining <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="There has to be at least one unit.")
    used = (await units.usage_counts(session)).get(row.name, 0)
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f'"{row.name}" is still used by {used} material{"" if used == 1 else "s or categories"}. '
            "Change those to another unit first.",
        )
    await session.delete(row)
    await session.commit()


@router.post("/reorder", response_model=list[MaterialUnitRead])
async def reorder_material_units(
    payload: MaterialUnitReorder, session: AsyncSession = Depends(get_db)
) -> list[MaterialUnitRow]:
    """Absolute positions from the given sequence, as for material categories."""
    rows = {
        row.id: row
        for row in (
            await session.execute(select(MaterialUnitRow).where(MaterialUnitRow.id.in_(payload.ids)))
        ).scalars()
    }
    missing = [row_id for row_id in payload.ids if row_id not in rows]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"No unit with id {missing[0]}.")
    for index, row_id in enumerate(payload.ids):
        rows[row_id].sort_order = (index + 1) * 10
    await session.commit()
    return await _list_with_usage(session)
