"""Recording, reversing, and closing off what actually arrived against a purchase order.

Two things here are worth reading before changing anything.

**A delivery is one fact, so it is one call.** Receiving is per line, but a van turning up
is not: it brings several lines at once, at one moment. So the whole delivery is one
request, one transaction, one status refresh and one batched recompute — rather than a
call per line that would leave the order visibly half-received in between and recompute
the same materials three times over.

**purchases.status is derived here and nowhere else.** It is a denormalisation kept for
the list filter and the pill in the UI; nothing about stock or cost reads it (see
services/costing.py, which replays the receipt rows themselves). refresh_purchase_status
is the only writer, and every path that touches lines or receipts has to call it.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.material import Material, MaterialAdjustment
from app.models.purchase import MaterialPurchase, MaterialPurchaseReceipt, Purchase, PurchaseStatus
from app.services.costing import recompute_materials
from app.services.platforms.base import ensure_utc
from app.services.validation import validate_qty_for_unit



async def refresh_purchase_status(purchase: Purchase) -> None:
    """Work the order's status out from its lines. The only writer of purchases.status.

    received_at goes with it, and means completion — the moment the last outstanding line
    was settled, by delivery or by being closed short. It is not a costing timestamp any
    more; each receipt carries its own.
    """
    if not any(line.receipts for line in purchase.lines):
        purchase.status = PurchaseStatus.ordered
        purchase.received_at = None
        return

    if all(line.outstanding_qty == 0 for line in purchase.lines):
        purchase.status = PurchaseStatus.received
        # ensure_utc because a receipt just added in this session is tz-aware while one
        # loaded back from SQLite is naive, and max() over the mixture raises.
        stamps = [ensure_utc(r.received_at) for line in purchase.lines for r in line.receipts]
        purchase.received_at = max(stamps) if stamps else datetime.now(timezone.utc)
        return

    purchase.status = PurchaseStatus.partially_received
    purchase.received_at = None


async def _load_purchase(session: AsyncSession, purchase_id: int) -> Purchase:
    result = await session.execute(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(selectinload(Purchase.lines).selectinload(MaterialPurchase.receipts))
        .execution_options(populate_existing=True)
    )
    purchase = result.scalar_one_or_none()
    if purchase is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Purchase not found")
    return purchase


async def _material_has_no_history(session: AsyncSession, material_id: int) -> bool:
    """True when nothing has ever moved this material — no receipt, no adjustment.

    This is the precondition for treating a first delivery as a count, and it is stricter
    than "the quantity is currently zero" on purpose. See record_receipts.
    """
    receipt = await session.execute(
        select(MaterialPurchaseReceipt.id)
        .join(MaterialPurchase, MaterialPurchase.id == MaterialPurchaseReceipt.purchase_line_id)
        .where(MaterialPurchase.material_id == material_id)
        .limit(1)
    )
    if receipt.scalar_one_or_none() is not None:
        return False
    adjustment = await session.execute(
        select(MaterialAdjustment.id).where(MaterialAdjustment.material_id == material_id).limit(1)
    )
    return adjustment.scalar_one_or_none() is None


async def record_receipts(
    session: AsyncSession,
    purchase_id: int,
    lines: list[tuple[int, Decimal, Decimal | None]],
    received_at: datetime | None = None,
    notes: str | None = None,
) -> Purchase:
    """Record one delivery against a purchase order. `lines` is (line_id, qty, total_cost?).

    Commits.
    """
    if not lines:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="Nothing to receive")

    purchase = await _load_purchase(session, purchase_id)
    by_id = {line.id: line for line in purchase.lines}
    at = received_at or datetime.now(timezone.utc)
    batch_id = uuid.uuid4().hex

    # Every check runs before anything is written, so a delivery with one bad line does not
    # half-apply and leave the user to work out which half.
    seen: set[int] = set()
    for line_id, qty, _ in lines:
        line = by_id.get(line_id)
        if line is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Line {line_id} is not on this purchase order",
            )
        if line_id in seen:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Line {line_id} appears twice in one delivery",
            )
        seen.add(line_id)
        if qty <= 0:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST, detail="Received quantity must be more than zero"
            )
        outstanding = line.outstanding_qty
        if qty > outstanding:
            material = await session.get(Material, line.material_id)
            name = material.name if material else f"line {line_id}"
            detail = (
                f"Cannot receive {qty} of {name} — only {outstanding} still outstanding"
                if outstanding > 0
                else f"Cannot receive {name} — that line is already complete"
            )
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=detail)
        material = await session.get(Material, line.material_id)
        if material is not None:
            validate_qty_for_unit(qty, material.unit, "qty")

    # A material whose ledger is empty has a *provable* zero, so the quantity after its
    # first delivery is exactly what was delivered — that is a verified count, and asking
    # someone to go and count the box they have just opened is busywork. Deliberately not
    # "any delivery onto a material at zero": a material that ran down to nothing through
    # consumption has an *unverified* zero, and that belief is precisely what a count is
    # for. Checked before the receipts are written, while the ledger is still empty.
    first_delivery_materials = set()
    for line_id, _, _ in lines:
        material_id = by_id[line_id].material_id
        if material_id in first_delivery_materials:
            continue
        material = await session.get(Material, material_id)
        if material is not None and material.last_stock_take_at is None:
            if await _material_has_no_history(session, material_id):
                first_delivery_materials.add(material_id)

    for line_id, qty, total_cost in lines:
        by_id[line_id].receipts.append(
            MaterialPurchaseReceipt(
                qty=qty, total_cost=total_cost, received_at=at, notes=notes, batch_id=batch_id
            )
        )

    await session.flush()
    await session.refresh(purchase, ["lines"])
    await refresh_purchase_status(purchase)

    for material_id in first_delivery_materials:
        material = await session.get(Material, material_id)
        if material is not None:
            material.last_stock_take_at = at

    await recompute_materials(session, {by_id[line_id].material_id for line_id, _, _ in lines})
    await session.commit()
    return await _load_purchase(session, purchase_id)


async def delete_receipts(
    session: AsyncSession,
    purchase_id: int,
    receipt_ids: list[int],
) -> Purchase:
    """Reverse one or more receipts. Commits."""
    purchase = await _load_purchase(session, purchase_id)
    by_receipt = {r.id: line for line in purchase.lines for r in line.receipts}

    missing = [rid for rid in receipt_ids if rid not in by_receipt]
    if missing:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"No such receipt on this purchase order: {', '.join(str(m) for m in missing)}",
        )

    material_ids = {by_receipt[rid].material_id for rid in receipt_ids}
    for line in purchase.lines:
        for receipt in list(line.receipts):
            if receipt.id in receipt_ids:
                line.receipts.remove(receipt)

    await session.flush()
    await session.refresh(purchase, ["lines"])
    await refresh_purchase_status(purchase)

    # Undoing the delivery that started a material's ledger should undo the count date it
    # stamped, or the item stays off the counting list on the strength of an event that no
    # longer happened. Only when nothing else could have set it: a real stock take records
    # its id, and a manual "set" adjustment leaves an adjustment row behind.
    for material_id in material_ids:
        material = await session.get(Material, material_id)
        if material is None or material.last_stock_take_id is not None:
            continue
        if await _material_has_no_history(session, material_id):
            material.last_stock_take_at = None

    await recompute_materials(session, material_ids)
    await session.commit()
    return await _load_purchase(session, purchase_id)


async def receipt_ids_for_batch(session: AsyncSession, purchase_id: int, batch_id: str) -> list[int]:
    purchase = await _load_purchase(session, purchase_id)
    return [r.id for line in purchase.lines for r in line.receipts if r.batch_id == batch_id]


async def close_line(
    session: AsyncSession, purchase_id: int, line_id: int, apportion_remainder: bool = False
) -> Purchase:
    """Declare that the rest of a line is never coming. Commits.

    apportion_remainder is for the supplier who billed the full line and shipped short: it
    writes whatever cost has not been taken up onto the last receipt, so the goods that did
    arrive carry the whole charge. Left off, the unreceived quantity's share of the cost
    simply never enters inventory, which is right when the invoice matched the delivery.
    """
    purchase = await _load_purchase(session, purchase_id)
    line = next((line for line in purchase.lines if line.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found on this purchase order")
    if line.closed_at is not None:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="That line is already closed")
    if line.outstanding_qty == 0:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="That line is already complete — there is nothing outstanding to close",
        )

    if apportion_remainder:
        if not line.receipts:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Nothing has been received on that line, so there is nothing to carry the cost",
            )
        last = max(line.receipts, key=lambda r: (r.received_at, r.id))
        already = sum(
            (Decimal(r.total_cost) for r in line.receipts if r is not last and r.total_cost is not None),
            Decimal(0),
        )
        pro_rata_of_others = sum(
            (
                Decimal(line.total_cost) * Decimal(r.qty) / Decimal(line.qty)
                for r in line.receipts
                if r is not last and r.total_cost is None
            ),
            Decimal(0),
        )
        remainder = Decimal(line.total_cost) - already - pro_rata_of_others
        last.total_cost = remainder if remainder > 0 else Decimal(0)

    line.closed_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(purchase, ["lines"])
    await refresh_purchase_status(purchase)
    await recompute_materials(session, {line.material_id})
    await session.commit()
    return await _load_purchase(session, purchase_id)


async def reopen_line(session: AsyncSession, purchase_id: int, line_id: int) -> Purchase:
    """Undo close_line. Commits.

    Any cost apportioned onto the last receipt when the line was closed stays put: it
    recorded what the supplier billed, which reopening the line does not un-say.
    """
    purchase = await _load_purchase(session, purchase_id)
    line = next((line for line in purchase.lines if line.id == line_id), None)
    if line is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Line not found on this purchase order")
    if line.closed_at is None:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="That line is not closed")

    line.closed_at = None
    await session.flush()
    await session.refresh(purchase, ["lines"])
    await refresh_purchase_status(purchase)
    await recompute_materials(session, {line.material_id})
    await session.commit()
    return await _load_purchase(session, purchase_id)
