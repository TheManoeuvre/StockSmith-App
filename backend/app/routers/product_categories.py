from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.product_category import ProductCategory
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.product_category import ProductCategoryCreate, ProductCategoryFindOrCreate, ProductCategoryRead, ProductCategoryUpdate

router = APIRouter(prefix="/product-categories", tags=["product-categories"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ProductCategoryRead])
async def list_product_categories(session: AsyncSession = Depends(get_db)) -> list[ProductCategory]:
    return await list_with_usage(session, ProductCategory)


@router.post("", response_model=ProductCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_product_category(payload: ProductCategoryCreate, session: AsyncSession = Depends(get_db)) -> ProductCategory:
    row = ProductCategory(**payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/find-or-create", response_model=ProductCategoryRead)
async def find_or_create_product_category(
    payload: ProductCategoryFindOrCreate, session: AsyncSession = Depends(get_db)
) -> ProductCategory:
    result = await session.execute(select(ProductCategory).where(ProductCategory.name == payload.name))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = ProductCategory(name=payload.name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{row_id}", response_model=ProductCategoryRead)
async def update_product_category(
    row_id: int, payload: ProductCategoryUpdate, session: AsyncSession = Depends(get_db)
) -> ProductCategory:
    return await patch_row(session, ProductCategory, row_id, payload)


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_category(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    await delete_row(session, ProductCategory, row_id)


@router.post("/{row_id}/merge", response_model=ProductCategoryRead)
async def merge_product_category(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> ProductCategory:
    """Repoint every product from this type onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one type.
    """
    return await merge_rows(session, ProductCategory, row_id, payload)
