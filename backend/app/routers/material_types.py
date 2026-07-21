from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.material_type import MaterialType
from app.schemas.material_type import MaterialTypeCreate, MaterialTypeFindOrCreate, MaterialTypeRead

router = APIRouter(prefix="/material-types", tags=["material-types"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[MaterialTypeRead])
async def list_material_types(session: AsyncSession = Depends(get_db)) -> list[MaterialType]:
    result = await session.execute(select(MaterialType).order_by(MaterialType.name))
    return list(result.scalars())


@router.post("", response_model=MaterialTypeRead, status_code=status.HTTP_201_CREATED)
async def create_material_type(payload: MaterialTypeCreate, session: AsyncSession = Depends(get_db)) -> MaterialType:
    material_type = MaterialType(**payload.model_dump())
    session.add(material_type)
    await session.commit()
    await session.refresh(material_type)
    return material_type


@router.post("/find-or-create", response_model=MaterialTypeRead)
async def find_or_create_material_type(
    payload: MaterialTypeFindOrCreate, session: AsyncSession = Depends(get_db)
) -> MaterialType:
    result = await session.execute(select(MaterialType).where(MaterialType.name == payload.name))
    material_type = result.scalar_one_or_none()
    if material_type is not None:
        return material_type
    material_type = MaterialType(name=payload.name)
    session.add(material_type)
    await session.commit()
    await session.refresh(material_type)
    return material_type
