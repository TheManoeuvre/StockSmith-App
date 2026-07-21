from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.manufacturer import Manufacturer
from app.schemas.manufacturer import ManufacturerCreate, ManufacturerFindOrCreate, ManufacturerRead

router = APIRouter(prefix="/manufacturers", tags=["manufacturers"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ManufacturerRead])
async def list_manufacturers(session: AsyncSession = Depends(get_db)) -> list[Manufacturer]:
    result = await session.execute(select(Manufacturer).order_by(Manufacturer.name))
    return list(result.scalars())


@router.post("", response_model=ManufacturerRead, status_code=status.HTTP_201_CREATED)
async def create_manufacturer(payload: ManufacturerCreate, session: AsyncSession = Depends(get_db)) -> Manufacturer:
    manufacturer = Manufacturer(**payload.model_dump())
    session.add(manufacturer)
    await session.commit()
    await session.refresh(manufacturer)
    return manufacturer


@router.post("/find-or-create", response_model=ManufacturerRead)
async def find_or_create_manufacturer(
    payload: ManufacturerFindOrCreate, session: AsyncSession = Depends(get_db)
) -> Manufacturer:
    result = await session.execute(select(Manufacturer).where(Manufacturer.name == payload.name))
    manufacturer = result.scalar_one_or_none()
    if manufacturer is not None:
        return manufacturer
    manufacturer = Manufacturer(name=payload.name)
    session.add(manufacturer)
    await session.commit()
    await session.refresh(manufacturer)
    return manufacturer
