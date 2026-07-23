from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.material import Material
from app.models.purchase import MaterialPurchase, Purchase
from app.schemas.material import DraftPurchaseCreate, MaterialAdjustmentCreate, MaterialCreate, MaterialRead, MaterialUpdate
from app.schemas.purchase import MaterialStockHistoryRead, PurchaseRead
from app.services.costing import create_adjustment, get_on_order_qty_by_material
from app.services.csv_io import export_materials_csv, import_materials_csv
from app.services.file_storage import delete_asset_file, resolve_asset_path, save_material_image, thumbnail_path_for
from app.services.url_import import fetch_image_bytes


class ImportImageUrlRequest(BaseModel):
    url: str


router = APIRouter(prefix="/materials", tags=["materials"], dependencies=[Depends(require_auth)])

_STOCK_HISTORY_SQL = text(
    """
    SELECT 'purchase' AS kind, mp.id AS id, CAST(p.order_date AS TIMESTAMP) AS at, mp.qty AS qty,
           mp.total_cost AS total_cost, p.status AS status, s.name AS supplier_name, NULL AS reason,
           NULL AS mode, NULL AS target_qty, CAST(NULL AS INTEGER) AS product_id, CAST(NULL AS TEXT) AS product_name,
           CAST(NULL AS INTEGER) AS variant_id, CAST(NULL AS INTEGER) AS order_id
    FROM material_purchases mp
    JOIN purchases p ON p.id = mp.purchase_id
    LEFT JOIN suppliers s ON s.id = p.supplier_id
    WHERE mp.material_id = :material_id

    UNION ALL

    SELECT 'adjustment' AS kind, ma.id AS id, ma.created_at AS at, ma.qty_delta AS qty,
           NULL AS total_cost, NULL AS status, NULL AS supplier_name, ma.reason AS reason,
           ma.mode AS mode, ma.target_qty AS target_qty, ma.product_id AS product_id, pr.name AS product_name,
           ma.variant_id AS variant_id, ma.order_id AS order_id
    FROM material_adjustments ma
    LEFT JOIN products pr ON pr.id = ma.product_id
    WHERE ma.material_id = :material_id

    ORDER BY at DESC, id DESC
    LIMIT :limit
    """
)


def _to_material_read(material: Material, on_order_qty_by_material: dict) -> MaterialRead:
    return MaterialRead.model_validate(material).model_copy(
        update={"on_order_qty": on_order_qty_by_material.get(material.id)}
    )


async def _get_material_with_manufacturer(session: AsyncSession, material_id: int) -> Material:
    result = await session.execute(
        select(Material)
        .where(Material.id == material_id)
        .options(
            selectinload(Material.manufacturer),
            selectinload(Material.default_supplier),
            selectinload(Material.material_type),
        )
        .execution_options(populate_existing=True)
    )
    material = result.scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    return material


@router.get("", response_model=list[MaterialRead])
async def list_materials(
    material_type_id: int | None = None, session: AsyncSession = Depends(get_db)
) -> list[MaterialRead]:
    query = (
        select(Material)
        .options(
            selectinload(Material.manufacturer),
            selectinload(Material.default_supplier),
            selectinload(Material.material_type),
        )
        .order_by(Material.name)
    )
    if material_type_id is not None:
        query = query.where(Material.material_type_id == material_type_id)
    result = await session.execute(query)
    materials = list(result.scalars())
    on_order_qty_by_material = await get_on_order_qty_by_material(session)
    return [_to_material_read(m, on_order_qty_by_material) for m in materials]


@router.get("/colours", response_model=list[str])
async def list_material_colours(session: AsyncSession = Depends(get_db)) -> list[str]:
    result = await session.execute(
        select(Material.colour).where(Material.colour.is_not(None), Material.colour != "").distinct().order_by(Material.colour)
    )
    return [row[0] for row in result]


@router.get("/export")
async def export_materials(session: AsyncSession = Depends(get_db)) -> Response:
    csv_text = await export_materials_csv(session)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=materials.csv"},
    )


@router.post("/import")
async def import_materials(file: UploadFile, session: AsyncSession = Depends(get_db)) -> dict:
    content = await file.read()
    return await import_materials_csv(session, content)


@router.post("", response_model=MaterialRead, status_code=status.HTTP_201_CREATED)
async def create_material(payload: MaterialCreate, session: AsyncSession = Depends(get_db)) -> Material:
    material = Material(**payload.model_dump())
    session.add(material)
    await session.commit()
    return await _get_material_with_manufacturer(session, material.id)


@router.get("/{material_id}", response_model=MaterialRead)
async def get_material(material_id: int, session: AsyncSession = Depends(get_db)) -> MaterialRead:
    material = await _get_material_with_manufacturer(session, material_id)
    on_order_qty_by_material = await get_on_order_qty_by_material(session)
    return _to_material_read(material, on_order_qty_by_material)


@router.patch("/{material_id}", response_model=MaterialRead)
async def update_material(
    material_id: int, payload: MaterialUpdate, session: AsyncSession = Depends(get_db)
) -> Material:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(material, field, value)
    await session.commit()
    return await _get_material_with_manufacturer(session, material_id)


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(material_id: int, session: AsyncSession = Depends(get_db)) -> None:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    material.is_active = False
    await session.commit()


@router.get("/{material_id}/stock-history", response_model=list[MaterialStockHistoryRead])
async def get_stock_history(
    material_id: int, limit: int = Query(10, ge=1, le=100000), session: AsyncSession = Depends(get_db)
) -> list[dict]:
    result = await session.execute(_STOCK_HISTORY_SQL, {"material_id": material_id, "limit": limit})
    return [dict(row._mapping) for row in result]


@router.post("/{material_id}/draft-purchase", response_model=PurchaseRead, status_code=status.HTTP_201_CREATED)
async def create_draft_purchase(
    material_id: int, payload: DraftPurchaseCreate, session: AsyncSession = Depends(get_db)
) -> Purchase:
    """Creates a pending (ordered) draft Purchase pre-filled from the material's
    remembered supplier/qty, so a low-stock alert can become a real purchase order
    in one click — the frontend navigates to the purchase's edit page afterwards so
    the user reviews/adjusts it before it means anything real."""
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    qty = payload.qty or material.typical_reorder_qty or Decimal("1")

    purchase = Purchase(supplier_id=material.default_supplier_id)
    purchase.lines = [MaterialPurchase(material_id=material_id, qty=qty, total_cost=Decimal("0"))]
    session.add(purchase)
    await session.commit()

    result = await session.execute(
        select(Purchase)
        .where(Purchase.id == purchase.id)
        .options(selectinload(Purchase.lines), selectinload(Purchase.supplier))
    )
    return result.scalar_one()


@router.post("/{material_id}/adjustments", response_model=MaterialRead)
async def create_material_adjustment(
    material_id: int, payload: MaterialAdjustmentCreate, session: AsyncSession = Depends(get_db)
) -> Material:
    await create_adjustment(session, material_id, payload.mode, payload.value, payload.reason)
    return await _get_material_with_manufacturer(session, material_id)


@router.post("/{material_id}/image", response_model=MaterialRead)
async def upload_material_image(
    material_id: int, request: Request, original_filename: str, session: AsyncSession = Depends(get_db)
) -> Material:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    if material.image_path:
        delete_asset_file(material.image_path)

    data = await request.body()
    relative_path, filename_used = save_material_image(material_id, material.name, original_filename, data)
    material.image_path = relative_path
    material.image_original_filename = filename_used
    await session.commit()
    return await _get_material_with_manufacturer(session, material_id)


@router.post("/{material_id}/image/import-url", response_model=MaterialRead)
async def import_material_image_from_url(
    material_id: int, payload: ImportImageUrlRequest, session: AsyncSession = Depends(get_db)
) -> Material:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")

    data, filename = await fetch_image_bytes(payload.url)

    if material.image_path:
        delete_asset_file(material.image_path)

    relative_path, filename_used = save_material_image(material_id, material.name, filename, data)
    material.image_path = relative_path
    material.image_original_filename = filename_used
    await session.commit()
    return await _get_material_with_manufacturer(session, material_id)


@router.get("/{material_id}/image/download")
async def download_material_image(material_id: int, session: AsyncSession = Depends(get_db)) -> FileResponse:
    material = await session.get(Material, material_id)
    if material is None or not material.image_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No image for this material")
    path = resolve_asset_path(material.image_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image file missing on disk")
    return FileResponse(path, filename=material.image_original_filename)


@router.get("/{material_id}/image/thumbnail")
async def download_material_image_thumbnail(material_id: int, session: AsyncSession = Depends(get_db)) -> FileResponse:
    material = await session.get(Material, material_id)
    if material is None or not material.image_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No image for this material")
    path = resolve_asset_path(thumbnail_path_for(material.image_path).as_posix())
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thumbnail missing on disk")
    return FileResponse(path, filename=f"thumb-{material.image_original_filename}")


@router.delete("/{material_id}/image", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_image(material_id: int, session: AsyncSession = Depends(get_db)) -> None:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
    if material.image_path:
        delete_asset_file(material.image_path)
    material.image_path = None
    material.image_original_filename = None
    await session.commit()
