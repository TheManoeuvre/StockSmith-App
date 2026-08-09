from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.material_type import MaterialType
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.material_type import MaterialTypeCreate, MaterialTypeFindOrCreate, MaterialTypeRead, MaterialTypeUpdate

router = APIRouter(prefix="/material-types", tags=["material-types"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[MaterialTypeRead])
async def list_material_types(session: AsyncSession = Depends(get_db)) -> list[MaterialType]:
    return await list_with_usage(session, MaterialType)


@router.post("", response_model=MaterialTypeRead, status_code=status.HTTP_201_CREATED)
async def create_material_type(payload: MaterialTypeCreate, session: AsyncSession = Depends(get_db)) -> MaterialType:
    row = MaterialType(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=MaterialTypeRead)
async def find_or_create_material_type(
    payload: MaterialTypeFindOrCreate, session: AsyncSession = Depends(get_db)
) -> MaterialType:
    result = await session.execute(select(MaterialType).where(MaterialType.name == payload.name))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = MaterialType(name=payload.name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=MaterialTypeRead)
async def update_material_type(
    row_id: int, payload: MaterialTypeUpdate, session: AsyncSession = Depends(get_db)
) -> MaterialType:
    return await patch_row(session, MaterialType, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_type(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, MaterialType, row_id)


@router.post("/{row_id}/merge", response_model=MaterialTypeRead)
async def merge_material_type(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> MaterialType:
    """Repoint everything from this entry onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one supplier.
    """
    return await merge_rows(session, MaterialType, row_id, payload)
