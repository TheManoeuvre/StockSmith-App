from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.supplier import Supplier
from app.routers._reference_crud import MergeRequest, delete_row, list_with_usage, merge_rows, patch_row
from app.schemas.supplier import SupplierCreate, SupplierFindOrCreate, SupplierRead, SupplierUpdate
from app.services import reference_data, supplier_logo
from app.services.file_storage import resolve_asset_path

router = APIRouter(prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(require_auth)])


async def _get_or_404(session: AsyncSession, row_id: int) -> Supplier:
    row = await session.get(Supplier, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Supplier not found")
    return row


async def _with_usage(session: AsyncSession, row: Supplier) -> Supplier:
    row.usage_count = (await reference_data.usage_counts(session, Supplier)).get(row.id, 0)
    return row


def _set_logo(row: Supplier, png: bytes, source: str) -> None:
    row.logo_path = supplier_logo.store_logo(row.id, png)
    row.logo_source = source
    row.logo_updated_at = datetime.now(timezone.utc)


def _clear_logo(row: Supplier) -> None:
    supplier_logo.remove_logo_file(row.logo_path)
    row.logo_path = None
    row.logo_source = None
    row.logo_updated_at = None


async def _sync_favicon(row: Supplier, keep_on_failure: bool = False) -> bool:
    """Make a favicon-sourced logo match the supplier's current website. An uploaded logo is
    left alone. Returns whether a favicon is now set. Never raises.

    When nothing is found the old favicon is dropped by default — after a website change it
    belongs to the wrong site, and none is better than the wrong one. A plain re-fetch of the
    same site passes keep_on_failure so a flaky request doesn't lose a good icon.
    """
    if row.logo_source == "upload":
        return True
    png = await supplier_logo.fetch_favicon(row.website_url) if row.website_url else None
    if png is None:
        if not keep_on_failure:
            _clear_logo(row)
        return False
    _set_logo(row, png, "favicon")
    return True


@router.get("", response_model=list[SupplierRead])
async def list_suppliers(session: AsyncSession = Depends(get_db)) -> list[Supplier]:
    return await list_with_usage(session, Supplier)


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
async def create_supplier(payload: SupplierCreate, session: AsyncSession = Depends(get_db)) -> Supplier:
    row = Supplier(**payload.model_dump())
    session.add(row)
    await session.flush()
    if row.website_url:
        await _sync_favicon(row)
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
    previous_url = (await _get_or_404(session, row_id)).website_url
    row = await patch_row(session, Supplier, row_id, payload)
    # Only when the URL actually changed: every other save (renaming, lead time) shouldn't cost a
    # network round trip or replace a logo that is fine.
    if "website_url" in payload.model_fields_set and (row.website_url or None) != (previous_url or None):
        await _sync_favicon(row)
        await session.commit()
        await session.refresh(row)
        await _with_usage(session, row)
    return row


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(row_id: int, session: AsyncSession = Depends(get_db)) -> None:
    logo_path = (await _get_or_404(session, row_id)).logo_path
    await delete_row(session, Supplier, row_id)
    supplier_logo.remove_logo_file(logo_path)


@router.post("/{row_id}/merge", response_model=SupplierRead)
async def merge_supplier(
    row_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> Supplier:
    """Repoint everything from this entry onto another, then delete this one.

    The cure for duplicates that find-or-create accumulated — two spellings of one supplier.
    """
    logo_path = (await _get_or_404(session, row_id)).logo_path
    target = await merge_rows(session, Supplier, row_id, payload)
    supplier_logo.remove_logo_file(logo_path)
    return target


@router.get("/{row_id}/logo")
async def get_supplier_logo(row_id: int, session: AsyncSession = Depends(get_db)) -> FileResponse:
    row = await _get_or_404(session, row_id)
    if not row.logo_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No logo for this supplier")
    path = resolve_asset_path(row.logo_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo file missing on disk")
    return FileResponse(path, media_type="image/png")


@router.post("/{row_id}/logo", response_model=SupplierRead)
async def upload_supplier_logo(row_id: int, request: Request, session: AsyncSession = Depends(get_db)) -> Supplier:
    """Upload a logo, overriding any favicon. The raw image is the request body, as with
    material images."""
    row = await _get_or_404(session, row_id)
    png = supplier_logo.logo_from_upload(await request.body())
    _set_logo(row, png, "upload")
    await session.commit()
    await session.refresh(row)
    return await _with_usage(session, row)


@router.delete("/{row_id}/logo", response_model=SupplierRead)
async def delete_supplier_logo(row_id: int, session: AsyncSession = Depends(get_db)) -> Supplier:
    """Remove the logo. If the supplier has a website, that drops back to its favicon — removing
    an upload means "stop overriding", not "show nothing"."""
    row = await _get_or_404(session, row_id)
    _clear_logo(row)
    if row.website_url:
        await _sync_favicon(row)
    await session.commit()
    await session.refresh(row)
    return await _with_usage(session, row)


@router.post("/{row_id}/logo/refresh", response_model=SupplierRead)
async def refresh_supplier_favicon(row_id: int, session: AsyncSession = Depends(get_db)) -> Supplier:
    """Fetch the favicon again — for a site that changed its icon, or a first fetch that failed."""
    row = await _get_or_404(session, row_id)
    if row.logo_source == "upload":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This supplier has an uploaded logo. Remove it to use the website's icon instead.",
        )
    if not row.website_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Add a website first")
    if not await _sync_favicon(row, keep_on_failure=True):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Couldn't find an icon on that website")
    await session.commit()
    await session.refresh(row)
    return await _with_usage(session, row)
