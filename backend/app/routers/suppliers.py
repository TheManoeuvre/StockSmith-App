from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.supplier import Supplier
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.supplier import SupplierCreate, SupplierFindOrCreate, SupplierRead, SupplierUpdate

router = APIRouter(prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(session: AsyncSession = Depends(get_db)) -> list[Supplier]:
    return await list_with_usage(session, Supplier)


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(payload: SupplierCreate, session: AsyncSession = Depends(get_db)) -> Supplier:
    row = Supplier(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=SupplierRead)
async def find_or_create_supplier(
    payload: SupplierFindOrCreate, session: AsyncSession = Depends(get_db)
) -> Supplier:
    result = await session.execute(select(Supplier).where(Supplier.name == payload.name))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = Supplier(name=payload.name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=SupplierRead)
async def update_supplier(
    row_id: int, payload: SupplierUpdate, session: AsyncSession = Depends(get_db)
) -> Supplier:
    return await patch_row(session, Supplier, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, Supplier, row_id)


@router.post("/{row_id}/merge", response_model=SupplierRead)
async def merge_supplier(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> Supplier:
    """Repoint everything from this entry onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one supplier.
    """
    return await merge_rows(session, Supplier, row_id, payload)
