from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.manufacturer import Manufacturer
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.manufacturer import ManufacturerCreate, ManufacturerFindOrCreate, ManufacturerRead, ManufacturerUpdate

router = APIRouter(prefix="/manufacturers", tags=["manufacturers"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ManufacturerRead])
async def list_manufacturers(session: AsyncSession = Depends(get_db)) -> list[Manufacturer]:
    return await list_with_usage(session, Manufacturer)


@router.post("", response_model=ManufacturerRead, status_code=status.HTTP_201_CREATED)
async def create_manufacturer(payload: ManufacturerCreate, session: AsyncSession = Depends(get_db)) -> Manufacturer:
    row = Manufacturer(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=ManufacturerRead)
async def find_or_create_manufacturer(
    payload: ManufacturerFindOrCreate, session: AsyncSession = Depends(get_db)
) -> Manufacturer:
    result = await session.execute(select(Manufacturer).where(Manufacturer.name == payload.name))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = Manufacturer(name=payload.name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=ManufacturerRead)
async def update_manufacturer(
    row_id: int, payload: ManufacturerUpdate, session: AsyncSession = Depends(get_db)
) -> Manufacturer:
    return await patch_row(session, Manufacturer, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_manufacturer(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, Manufacturer, row_id)


@router.post("/{row_id}/merge", response_model=ManufacturerRead)
async def merge_manufacturer(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> Manufacturer:
    """Repoint everything from this entry onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one supplier.
    """
    return await merge_rows(session, Manufacturer, row_id, payload)
