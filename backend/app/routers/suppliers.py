from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.supplier import Supplier
from app.schemas.supplier import SupplierCreate, SupplierFindOrCreate, SupplierRead

router = APIRouter(prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(session: AsyncSession = Depends(get_db)) -> list[Supplier]:
    result = await session.execute(select(Supplier).order_by(Supplier.name))
    return list(result.scalars())


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(payload: SupplierCreate, session: AsyncSession = Depends(get_db)) -> Supplier:
    supplier = Supplier(**payload.model_dump())
    session.add(supplier)
    await session.commit()
    await session.refresh(supplier)
    return supplier


@router.post("/find-or-create", response_model=SupplierRead)
async def find_or_create_supplier(
    payload: SupplierFindOrCreate, session: AsyncSession = Depends(get_db)
) -> Supplier:
    result = await session.execute(select(Supplier).where(Supplier.name == payload.name))
    supplier = result.scalar_one_or_none()
    if supplier is not None:
        return supplier
    supplier = Supplier(name=payload.name)
    session.add(supplier)
    await session.commit()
    await session.refresh(supplier)
    return supplier
