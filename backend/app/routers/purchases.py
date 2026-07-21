from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.schemas.purchase import PurchaseCreate, PurchaseLineInput, PurchaseRead, PurchaseUpdate
from app.services.costing import recompute_materials
from app.services.validation import validate_lines_against_units

router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(require_auth)])


async def _get_purchase_with_lines(session: AsyncSession, purchase_id: int) -> Purchase:
    # populate_existing forces a fresh load of `lines` even if this Purchase is already
    # in the session's identity map with a stale relationship — matters because several
    # endpoints below fetch the same purchase twice in one request (once before mutating
    # lines, once after) and would otherwise see cached pre-mutation data on the second call.
    result = await session.execute(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(selectinload(Purchase.lines), selectinload(Purchase.supplier))
        .execution_options(populate_existing=True)
    )
    purchase = result.scalar_one_or_none()
    if purchase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found")
    return purchase


@router.get("", response_model=list[PurchaseRead])
async def list_purchases(
    status_filter: PurchaseStatus | None = None, session: AsyncSession = Depends(get_db)
) -> list[Purchase]:
    query = (
        select(Purchase)
        .options(selectinload(Purchase.lines), selectinload(Purchase.supplier))
        .order_by(Purchase.order_date.desc(), Purchase.id.desc())
    )
    if status_filter is not None:
        query = query.where(Purchase.status == status_filter)
    result = await session.execute(query)
    return list(result.scalars())


@router.post("", response_model=PurchaseRead, status_code=status.HTTP_201_CREATED)
async def create_purchase(payload: PurchaseCreate, session: AsyncSession = Depends(get_db)) -> Purchase:
    if not payload.lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A purchase needs at least one line")

    await validate_lines_against_units(session, [(l.material_id, l.qty) for l in payload.lines], "qty")

    purchase = Purchase(
        supplier_id=payload.supplier_id,
        notes=payload.notes,
        **({"order_date": payload.order_date} if payload.order_date else {}),
    )
    purchase.lines = [
        MaterialPurchase(material_id=l.material_id, qty=l.qty, total_cost=l.total_cost, notes=l.notes)
        for l in payload.lines
    ]
    session.add(purchase)
    await session.commit()
    return await _get_purchase_with_lines(session, purchase.id)


@router.get("/{purchase_id}", response_model=PurchaseRead)
async def get_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    return await _get_purchase_with_lines(session, purchase_id)


@router.patch("/{purchase_id}", response_model=PurchaseRead)
async def update_purchase(purchase_id: int, payload: PurchaseUpdate, session: AsyncSession = Depends(get_db)) -> Purchase:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(purchase, field, value)
    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)


@router.put("/{purchase_id}/lines", response_model=PurchaseRead)
async def replace_purchase_lines(
    purchase_id: int, payload: list[PurchaseLineInput], session: AsyncSession = Depends(get_db)
) -> Purchase:
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A purchase needs at least one line")

    purchase = await _get_purchase_with_lines(session, purchase_id)
    old_material_ids = {line.material_id for line in purchase.lines}

    await validate_lines_against_units(session, [(l.material_id, l.qty) for l in payload], "qty")

    await session.execute(delete(MaterialPurchase).where(MaterialPurchase.purchase_id == purchase_id))
    new_lines = [
        MaterialPurchase(purchase_id=purchase_id, material_id=l.material_id, qty=l.qty, total_cost=l.total_cost, notes=l.notes)
        for l in payload
    ]
    session.add_all(new_lines)
    await session.flush()

    new_material_ids = {l.material_id for l in payload}
    if purchase.status == PurchaseStatus.received:
        await recompute_materials(session, old_material_ids | new_material_ids)

    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> None:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    material_ids = {line.material_id for line in purchase.lines}
    was_received = purchase.status == PurchaseStatus.received

    await session.delete(purchase)
    await session.flush()

    if was_received:
        await recompute_materials(session, material_ids)

    await session.commit()


@router.post("/{purchase_id}/receive", response_model=PurchaseRead)
async def receive_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    if purchase.status == PurchaseStatus.received:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Purchase is already received")

    purchase.status = PurchaseStatus.received
    purchase.received_at = datetime.now(timezone.utc)
    await session.flush()

    material_ids = {line.material_id for line in purchase.lines}
    await recompute_materials(session, material_ids)

    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)


@router.post("/{purchase_id}/unreceive", response_model=PurchaseRead)
async def unreceive_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    if purchase.status != PurchaseStatus.received:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Purchase is not received")

    material_ids = {line.material_id for line in purchase.lines}
    purchase.status = PurchaseStatus.ordered
    purchase.received_at = None
    await session.flush()

    await recompute_materials(session, material_ids)

    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)
