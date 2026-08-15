from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.product_type import ProductType
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.product_type import ProductTypeCreate, ProductTypeFindOrCreate, ProductTypeRead, ProductTypeUpdate

router = APIRouter(prefix="/product-types", tags=["product-types"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ProductTypeRead])
async def list_product_types(session: AsyncSession = Depends(get_db)) -> list[ProductType]:
    return await list_with_usage(session, ProductType)


@router.post("", response_model=ProductTypeRead, status_code=status.HTTP_201_CREATED)
async def create_product_type(payload: ProductTypeCreate, session: AsyncSession = Depends(get_db)) -> ProductType:
    row = ProductType(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=ProductTypeRead)
async def find_or_create_product_type(
    payload: ProductTypeFindOrCreate, session: AsyncSession = Depends(get_db)
) -> ProductType:
    result = await session.execute(select(ProductType).where(ProductType.name == payload.name))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = ProductType(name=payload.name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=ProductTypeRead)
async def update_product_type(
    row_id: int, payload: ProductTypeUpdate, session: AsyncSession = Depends(get_db)
) -> ProductType:
    return await patch_row(session, ProductType, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_type(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, ProductType, row_id)


@router.post("/{row_id}/merge", response_model=ProductTypeRead)
async def merge_product_type(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> ProductType:
    """Repoint every product from this type onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one type.
    """
    return await merge_rows(session, ProductType, row_id, payload)
