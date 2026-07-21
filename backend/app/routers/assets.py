from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.asset import AssetType, ProductAsset
from app.models.product import Product
from app.schemas.asset import AssetRead, AssetUpdate
from app.services.file_storage import delete_asset_file, resolve_asset_path, save_upload, thumbnail_path_for
from app.services.url_import import fetch_image_bytes


class ImportUrlRequest(BaseModel):
    url: str


router = APIRouter(tags=["assets"], dependencies=[Depends(require_auth)])


@router.get("/products/{product_id}/assets", response_model=list[AssetRead])
async def list_assets(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductAsset]:
    result = await session.execute(
        select(ProductAsset).where(ProductAsset.product_id == product_id).order_by(ProductAsset.display_order)
    )
    return list(result.scalars())


@router.post("/products/{product_id}/assets", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    product_id: int,
    request: Request,
    asset_type: AssetType,
    original_filename: str,
    variant_id: int | None = None,
    display_order: int = 0,
    session: AsyncSession = Depends(get_db),
) -> ProductAsset:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    data = await request.body()
    relative_path, filename_used = save_upload(product_id, product.name, asset_type, original_filename, data)

    asset = ProductAsset(
        product_id=product_id,
        variant_id=variant_id,
        asset_type=asset_type,
        file_path=relative_path,
        original_filename=filename_used,
        display_order=display_order,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@router.post("/products/{product_id}/assets/import-url", response_model=AssetRead, status_code=status.HTTP_201_CREATED)
async def import_asset_from_url(
    product_id: int,
    payload: ImportUrlRequest,
    asset_type: AssetType,
    variant_id: int | None = None,
    display_order: int = 0,
    session: AsyncSession = Depends(get_db),
) -> ProductAsset:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    data, filename = await fetch_image_bytes(payload.url)
    relative_path, filename_used = save_upload(product_id, product.name, asset_type, filename, data)

    asset = ProductAsset(
        product_id=product_id,
        variant_id=variant_id,
        asset_type=asset_type,
        file_path=relative_path,
        original_filename=filename_used,
        display_order=display_order,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@router.patch("/assets/{asset_id}", response_model=AssetRead)
async def update_asset(asset_id: int, payload: AssetUpdate, session: AsyncSession = Depends(get_db)) -> ProductAsset:
    asset = await session.get(ProductAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    await session.commit()
    await session.refresh(asset)
    return asset


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: int, session: AsyncSession = Depends(get_db)) -> None:
    asset = await session.get(ProductAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    delete_asset_file(asset.file_path)
    await session.delete(asset)
    await session.commit()


@router.get("/assets/{asset_id}/download")
async def download_asset(asset_id: int, session: AsyncSession = Depends(get_db)) -> FileResponse:
    asset = await session.get(ProductAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    path = resolve_asset_path(asset.file_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file missing on disk")
    return FileResponse(path, filename=asset.original_filename)


@router.get("/assets/{asset_id}/thumbnail")
async def download_asset_thumbnail(asset_id: int, session: AsyncSession = Depends(get_db)) -> FileResponse:
    asset = await session.get(ProductAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    path = resolve_asset_path(thumbnail_path_for(asset.file_path).as_posix())
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thumbnail missing on disk")
    return FileResponse(path, filename=f"thumb-{asset.original_filename}")
