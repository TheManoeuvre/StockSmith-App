from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.material_category import MaterialCategory
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.material_category import (
    MaterialCategoryCreate,
    MaterialCategoryFindOrCreate,
    MaterialCategoryRead,
    MaterialCategoryReorder,
    MaterialCategoryUpdate,
)
from app.services import material_categories

router = APIRouter(
    prefix="/material-categories", tags=["material-categories"], dependencies=[Depends(require_auth)]
)

# Total and stable. sort_order alone isn't: nothing in the schema stops two rows sharing a
# value, and a reorder that only touched some of them could produce exactly that.
_ORDER = (MaterialCategory.sort_order, MaterialCategory.name, MaterialCategory.id)


@router.get("", response_model=list[MaterialCategoryRead])
async def list_material_categories(session: AsyncSession = Depends(get_db)) -> list[MaterialCategory]:
    return await list_with_usage(session, MaterialCategory, order_by=_ORDER)


@router.post("", response_model=MaterialCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_material_category(
    payload: MaterialCategoryCreate, session: AsyncSession = Depends(get_db)
) -> MaterialCategory:
    data = payload.model_dump()
    if not data.get("sort_order"):
        data["sort_order"] = await material_categories.next_sort_order(session)
    row = MaterialCategory(**data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=MaterialCategoryRead)
async def find_or_create_material_category(
    payload: MaterialCategoryFindOrCreate, session: AsyncSession = Depends(get_db)
) -> MaterialCategory:
    """Case-insensitive — see services/material_categories.find_or_create for why that matters
    more here than it did for the tables whose values were always chosen from a list."""
    row = await material_categories.find_or_create(session, payload.name)
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name cannot be empty.")
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=MaterialCategoryRead)
async def update_material_category(
    row_id: int, payload: MaterialCategoryUpdate, session: AsyncSession = Depends(get_db)
) -> MaterialCategory:
    row = await patch_row(session, MaterialCategory, row_id, payload)
    # A rename can move this category's materials in or out of the legacy vocabulary — calling
    # it "Filament PLA" means they can no longer store 'filament'. The generic layer has no way
    # to know that, and no other reference table has a legacy column to keep in step.
    await material_categories.sync_legacy_column(session, row_id)
    return row


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_category(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    # Every material must have a category, so deleting the last one leaves a state with no way
    # back through the UI. delete_if_unused can't catch this: with no materials at all, the
    # last category is legitimately unused.
    remaining = (await session.execute(select(func.count()).select_from(MaterialCategory))).scalar_one()
    if remaining <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="There has to be at least one category."
        )
    await delete_row(session, MaterialCategory, row_id)


@router.post("/{row_id}/merge", response_model=MaterialCategoryRead)
async def merge_material_category(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> MaterialCategory:
    """Repoint every material from this category onto another, then delete this one."""
    target = await merge_rows(session, MaterialCategory, row_id, payload)
    # The materials just changed category, so the legacy value they store is now wrong.
    await material_categories.sync_legacy_column(session, target.id)
    return target


@router.post("/reorder", response_model=list[MaterialCategoryRead])
async def reorder_material_categories(
    payload: MaterialCategoryReorder, session: AsyncSession = Depends(get_db)
) -> list[MaterialCategory]:
    """Rewrite sort_order from the given sequence.

    Absolute positions rather than a swap, so the result doesn't depend on what the client
    believed the previous order was. Ids the client didn't send keep their current value and
    sort among themselves by the tiebreakers.
    """
    rows = {
        row.id: row
        for row in (
            await session.execute(select(MaterialCategory).where(MaterialCategory.id.in_(payload.ids)))
        ).scalars()
    }
    missing = [row_id for row_id in payload.ids if row_id not in rows]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"No category with id {missing[0]}.")

    for index, row_id in enumerate(payload.ids):
        rows[row_id].sort_order = (index + 1) * 10
    await session.commit()
    return await list_with_usage(session, MaterialCategory, order_by=_ORDER)
