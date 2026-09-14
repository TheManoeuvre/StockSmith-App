from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.schemas.purchase import (
    PriceReferenceEntry,
    PriceReferenceRequest,
    PurchaseCreate,
    PurchaseLineCloseInput,
    PurchaseLineInput,
    PurchaseRead,
    PurchaseReceiptsCreate,
    PurchaseUpdate,
)
from app.services import purchase_receipts
from app.services.costing import recompute_materials
from app.services.csv_io import export_purchases_csv
from app.services.validation import validate_lines_against_units

router = APIRouter(prefix="/purchases", tags=["purchases"], dependencies=[Depends(require_auth)])


def _clean_supplier_order_number(value: str | None) -> str | None:
    """Blank or whitespace-only means "no supplier reference" — store NULL, not "".

    Keeps the list view's "show the supplier number if there is one" check a plain None test.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def _get_purchase_with_lines(session: AsyncSession, purchase_id: int) -> Purchase:
    # populate_existing forces a fresh load of `lines` even if this Purchase is already
    # in the session's identity map with a stale relationship — matters because several
    # endpoints below fetch the same purchase twice in one request (once before mutating
    # lines, once after) and would otherwise see cached pre-mutation data on the second call.
    result = await session.execute(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(
            selectinload(Purchase.lines).selectinload(MaterialPurchase.receipts),
            selectinload(Purchase.supplier),
        )
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
        .options(
            selectinload(Purchase.lines).selectinload(MaterialPurchase.receipts),
            selectinload(Purchase.supplier),
        )
        .order_by(Purchase.order_date.desc(), Purchase.id.desc())
    )
    if status_filter is not None:
        query = query.where(Purchase.status == status_filter)
    result = await session.execute(query)
    return list(result.scalars())


@router.get("/export")
async def export_purchases(session: AsyncSession = Depends(get_db)) -> Response:
    csv_text = await export_purchases_csv(session)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=purchases.csv"},
    )


@router.post("", response_model=PurchaseRead, status_code=status.HTTP_201_CREATED)
async def create_purchase(payload: PurchaseCreate, session: AsyncSession = Depends(get_db)) -> Purchase:
    if not payload.lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A purchase needs at least one line")

    await validate_lines_against_units(session, [(l.material_id, l.qty) for l in payload.lines], "qty")

    purchase = Purchase(
        supplier_id=payload.supplier_id,
        supplier_order_number=_clean_supplier_order_number(payload.supplier_order_number),
        notes=payload.notes,
        delivery_cost=payload.delivery_cost,
        expected_arrival_date=payload.expected_arrival_date,
        **({"order_date": payload.order_date} if payload.order_date else {}),
    )
    purchase.lines = [
        MaterialPurchase(material_id=l.material_id, qty=l.qty, total_cost=l.total_cost, notes=l.notes)
        for l in payload.lines
    ]
    session.add(purchase)
    await session.commit()
    return await _get_purchase_with_lines(session, purchase.id)


@router.post("/price-reference", response_model=list[PriceReferenceEntry])
async def price_reference(
    payload: PriceReferenceRequest, session: AsyncSession = Depends(get_db)
) -> list[PriceReferenceEntry]:
    """What each material last cost, so the new-purchase panel can flag a line priced above it.

    Considers any ordered line with a positive invoiced total — not only lines that have been
    delivered — because the invoiced line total is recorded at PO time. Prefers the most
    recent line from `supplier_id` when one is given, falling back to the most recent from any
    supplier. Materials with no such line are simply absent from the result.
    """
    if not payload.material_ids:
        return []

    result = await session.execute(
        select(MaterialPurchase, Purchase)
        .join(Purchase, MaterialPurchase.purchase_id == Purchase.id)
        .where(
            MaterialPurchase.material_id.in_(payload.material_ids),
            MaterialPurchase.qty > 0,
            MaterialPurchase.total_cost > 0,
        )
        .options(selectinload(Purchase.supplier))
        .order_by(Purchase.order_date.desc(), Purchase.id.desc())
    )

    # Rows arrive newest-first, so the first row seen for a material is the fallback pick; it
    # is only replaced when a later row is from the supplier asked about and the current pick
    # is not (which then also stops any older same-supplier row from displacing it).
    chosen: dict[int, tuple[MaterialPurchase, Purchase]] = {}
    for line, purchase in result.all():
        current = chosen.get(line.material_id)
        if current is None:
            chosen[line.material_id] = (line, purchase)
        elif (
            payload.supplier_id is not None
            and current[1].supplier_id != payload.supplier_id
            and purchase.supplier_id == payload.supplier_id
        ):
            chosen[line.material_id] = (line, purchase)

    return [
        PriceReferenceEntry(
            material_id=material_id,
            unit_cost=line.total_cost / line.qty,
            qty=line.qty,
            total_cost=line.total_cost,
            supplier_id=purchase.supplier_id,
            supplier_name=purchase.supplier_name,
            purchase_id=purchase.id,
            purchase_ref=purchase.supplier_order_number,
            at=purchase.order_date,
            same_supplier=(
                payload.supplier_id is not None
                and purchase.supplier_id == payload.supplier_id
            ),
        )
        for material_id, (line, purchase) in chosen.items()
    ]


@router.get("/{purchase_id}", response_model=PurchaseRead)
async def get_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    return await _get_purchase_with_lines(session, purchase_id)


@router.patch("/{purchase_id}", response_model=PurchaseRead)
async def update_purchase(purchase_id: int, payload: PurchaseUpdate, session: AsyncSession = Depends(get_db)) -> Purchase:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "supplier_order_number":
            value = _clean_supplier_order_number(value)
        setattr(purchase, field, value)
    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)


@router.put("/{purchase_id}/lines", response_model=PurchaseRead)
async def replace_purchase_lines(
    purchase_id: int, payload: list[PurchaseLineInput], session: AsyncSession = Depends(get_db)
) -> Purchase:
    """Match the order's lines to what was sent, keeping the lines that already exist.

    This used to delete every line and reinsert them, which was harmless while a line was
    just a row of numbers — and became data loss the moment receipts started hanging off
    line ids, because the detail page calls this on every save, including a save that only
    changed the notes.

    So it is an upsert now, and it refuses rather than guesses whenever honouring the
    payload would contradict something that has physically arrived.
    """
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A purchase needs at least one line")

    purchase = await _get_purchase_with_lines(session, purchase_id)
    existing = {line.id: line for line in purchase.lines}
    has_receipts = any(line.receipts for line in purchase.lines)
    old_material_ids = {line.material_id for line in purchase.lines}

    await validate_lines_against_units(session, [(l.material_id, l.qty) for l in payload], "qty")

    payload_ids = {l.id for l in payload if l.id is not None}
    unknown = payload_ids - existing.keys()
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Line {sorted(unknown)[0]} is not on this purchase order",
        )

    # A payload with no ids against an order that has receipts is a stale client — the page
    # was loaded before line identity mattered. Failing here is much better than the
    # alternative, which is silently duplicating every line and orphaning the receipts.
    if has_receipts and any(l.id is None for l in payload) and payload_ids != existing.keys():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This purchase order has been received against — reload the page before editing its lines",
        )

    removed = [line for line_id, line in existing.items() if line_id not in payload_ids]
    for line in removed:
        if line.receipts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That line has already been received against, so it cannot be removed. Close it short instead.",
            )

    for incoming in payload:
        if incoming.id is None:
            purchase.lines.append(
                MaterialPurchase(
                    material_id=incoming.material_id,
                    qty=incoming.qty,
                    total_cost=incoming.total_cost,
                    notes=incoming.notes,
                )
            )
            continue

        line = existing[incoming.id]
        if line.receipts and incoming.material_id != line.material_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That line has already been received against, so its material cannot be changed",
            )
        if incoming.qty < line.received_qty:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{line.received_qty} has already been received on that line, so it cannot be reduced below that",
            )
        line.material_id = incoming.material_id
        line.qty = incoming.qty
        line.total_cost = incoming.total_cost
        line.notes = incoming.notes

    for line in removed:
        purchase.lines.remove(line)

    await session.flush()
    await session.refresh(purchase, ["lines"])

    # Editing a line total reprices its receipts, since a delivery takes a share of it —
    # so a cost-only edit has to recompute too, not just one that moved quantities around.
    # Over the union of old and new materials, unconditionally: an order has a handful of
    # lines, and being cleverer here buys nothing but a way to be wrong.
    if any(line.receipts for line in purchase.lines):
        await purchase_receipts.refresh_purchase_status(purchase)
        await recompute_materials(session, old_material_ids | {l.material_id for l in payload})

    await session.commit()
    return await _get_purchase_with_lines(session, purchase_id)


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> None:
    purchase = await _get_purchase_with_lines(session, purchase_id)
    material_ids = {line.material_id for line in purchase.lines}
    had_receipts = any(line.receipts for line in purchase.lines)

    await session.delete(purchase)
    await session.flush()

    if had_receipts:
        await recompute_materials(session, material_ids)

    await session.commit()


@router.post("/{purchase_id}/receipts", response_model=PurchaseRead)
async def create_receipts(
    purchase_id: int, payload: PurchaseReceiptsCreate, session: AsyncSession = Depends(get_db)
) -> Purchase:
    """Record one delivery — however many lines of the order arrived in it."""
    return await purchase_receipts.record_receipts(
        session,
        purchase_id,
        [(l.line_id, l.qty, l.total_cost) for l in payload.lines],
        received_at=payload.received_at,
        notes=payload.notes,
    )


@router.delete("/{purchase_id}/receipts/{receipt_id}", response_model=PurchaseRead)
async def delete_receipt(purchase_id: int, receipt_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    return await purchase_receipts.delete_receipts(session, purchase_id, [receipt_id])


@router.delete("/{purchase_id}/receipts", response_model=PurchaseRead)
async def delete_receipt_batch(
    purchase_id: int, batch_id: str = Query(...), session: AsyncSession = Depends(get_db)
) -> Purchase:
    """Undo a whole delivery, rather than picking its lines off one at a time."""
    receipt_ids = await purchase_receipts.receipt_ids_for_batch(session, purchase_id, batch_id)
    if not receipt_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such delivery on this purchase order")
    return await purchase_receipts.delete_receipts(session, purchase_id, receipt_ids)


@router.post("/{purchase_id}/lines/{line_id}/close", response_model=PurchaseRead)
async def close_purchase_line(
    purchase_id: int,
    line_id: int,
    payload: PurchaseLineCloseInput | None = None,
    session: AsyncSession = Depends(get_db),
) -> Purchase:
    """Declare that the rest of a line is never coming."""
    apportion = payload.apportion_remainder if payload is not None else False
    return await purchase_receipts.close_line(session, purchase_id, line_id, apportion_remainder=apportion)


@router.post("/{purchase_id}/lines/{line_id}/reopen", response_model=PurchaseRead)
async def reopen_purchase_line(purchase_id: int, line_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    return await purchase_receipts.reopen_line(session, purchase_id, line_id)


@router.post("/{purchase_id}/receive", response_model=PurchaseRead)
async def receive_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    """Receive everything still outstanding, now — the whole-order shorthand.

    Kept because it is the overwhelmingly common case (the order turned up, all of it) and
    because it is one click from the list page. It is the receipts endpoint underneath.
    """
    purchase = await _get_purchase_with_lines(session, purchase_id)
    lines = [(line.id, line.outstanding_qty, None) for line in purchase.lines if line.outstanding_qty > 0]
    if not lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Purchase is already received")
    return await purchase_receipts.record_receipts(
        session, purchase_id, lines, received_at=datetime.now(timezone.utc)
    )


@router.post("/{purchase_id}/unreceive", response_model=PurchaseRead)
async def unreceive_purchase(purchase_id: int, session: AsyncSession = Depends(get_db)) -> Purchase:
    """Undo every delivery recorded against this order.

    The blunt instrument. Individual deliveries can be reversed on their own, which is
    usually what someone actually wants when one line was keyed wrong.
    """
    purchase = await _get_purchase_with_lines(session, purchase_id)
    receipt_ids = [r.id for line in purchase.lines for r in line.receipts]
    if not receipt_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Purchase is not received")
    return await purchase_receipts.delete_receipts(session, purchase_id, receipt_ids)
