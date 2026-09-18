"""Replacement parcels and the marketplace shipping labels behind them.

Two entry points feed the same tables:

- The user records a parcel by hand (create_manual_parcel): products and packaging
  leave stock immediately, their costs are frozen, and postage is either a typed figure
  or a synced marketplace label they pick. Delete (delete_parcel) reverses all of it.
- A marketplace sync reports the labels bought against an order
  (apply_postage_charges): label #1 is the original shipment and only affects the
  postage figure profit uses; every later label is a resend, and is linked to an
  existing unlinked parcel or spawns a needs_review one — with the label's cost, no
  items and no reason, plus an alert asking the user to complete it.

Neither path touches OrderLine, the kitting ledger or allocation state — see
models/order_parcel.py for why a replacement is deliberately not sale demand.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.listing import ListingPlatform
from app.models.material import Material, MaterialAdjustment
from app.models.notification import NotificationCategory
from app.models.order import Order, OrderStatus
from app.models.order_parcel import (
    OrderPostageCharge,
    OrderReplacementParcel,
    OrderReplacementParcelItem,
    PostageChargeSource,
    ReplacementParcelReason,
    ReplacementParcelSource,
)
from app.models.product import Product
from app.models.product_stock_event import ProductStockEventType
from app.models.variant import ProductVariant
from app.schemas.order_parcel import ReplacementParcelCreate, ReplacementParcelUpdate
from app.services import listing_push
from app.services.costing import recompute_material
from app.services.notification_alerts import PendingReviewAlert, raise_replacement_parcel_review_alert
from app.services.notifications import resolve_alerts
from app.services.order_costs import compute_line_cost_snapshot
from app.services.platforms.base import ExternalPostageCharge
from app.services.stock_events import record_stock_event


@dataclass
class ReplacementCosts:
    """Per-order aggregate for net profit — see routers/orders._compute_net_profit.

    first_label_amount: the marketplace's actual cost for label #1 (the original shipment),
    None when no label has been synced OR the one synced is a bulk purchase with no
    per-order amount (OrderPostageCharge.amount NULL) — profit then falls back to the
    shipping-profile snapshot either way. parcel_postage: Σ over replacement parcels of
    the figure each one actually uses (its linked label's amount when it has one, else its
    typed postage_cost). items_cogs: Σ qty ×
    frozen unit cost over every parcel item — None-not-zero when no parcel has items, so a
    caller can render "—" rather than a confident zero."""

    first_label_amount: Decimal | None = None
    parcel_postage: Decimal = Decimal(0)
    items_cogs: Decimal | None = None


_PARCEL_POSTAGE_ROWS_SQL = text(
    """
    SELECT p.order_id,
           p.postage_cost AS manual_postage,
           c.amount       AS charge_amount
    FROM order_replacement_parcels p
    LEFT JOIN order_postage_charges c ON c.replacement_parcel_id = p.id
    WHERE p.order_id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))

_FIRST_LABEL_ROWS_SQL = text(
    """
    SELECT order_id, amount
    FROM order_postage_charges
    WHERE order_id IN :ids AND sequence = 1 AND amount IS NOT NULL
    """
).bindparams(bindparam("ids", expanding=True))

_ITEM_COST_ROWS_SQL = text(
    """
    SELECT p.order_id, i.qty, i.unit_cost_snapshot
    FROM order_replacement_parcel_items i
    JOIN order_replacement_parcels p ON p.id = i.parcel_id
    WHERE p.order_id IN :ids AND i.unit_cost_snapshot IS NOT NULL
    """
).bindparams(bindparam("ids", expanding=True))


async def get_replacement_costs_by_order(session: AsyncSession, order_ids: Sequence[int]) -> dict[int, ReplacementCosts]:
    """One round of queries for a whole page of orders (list_orders serves up to 200),
    mirroring kitting.get_kitting_cogs_by_order — including its rule of converting each
    factor via Decimal(str(...)) and summing in Python, because SQLite hands these Numeric
    columns back as floats and SUM() would accumulate binary error into net_profit."""
    if not order_ids:
        return {}
    ids = list(order_ids)
    totals: dict[int, ReplacementCosts] = {}

    def _get(order_id: int) -> ReplacementCosts:
        if order_id not in totals:
            totals[order_id] = ReplacementCosts()
        return totals[order_id]

    for row in await session.execute(_FIRST_LABEL_ROWS_SQL, {"ids": ids}):
        _get(row.order_id).first_label_amount = Decimal(str(row.amount))
    for row in await session.execute(_PARCEL_POSTAGE_ROWS_SQL, {"ids": ids}):
        figure = row.charge_amount if row.charge_amount is not None else row.manual_postage
        if figure is not None:
            agg = _get(row.order_id)
            agg.parcel_postage += Decimal(str(figure))
    for row in await session.execute(_ITEM_COST_ROWS_SQL, {"ids": ids}):
        agg = _get(row.order_id)
        agg.items_cogs = (agg.items_cogs or Decimal(0)) + Decimal(str(row.qty)) * Decimal(str(row.unit_cost_snapshot))
    return totals


# --- Manual parcels ---------------------------------------------------------------------


async def _get_charge_for_link(session: AsyncSession, order: Order, charge_id: int) -> OrderPostageCharge:
    charge = await session.get(OrderPostageCharge, charge_id)
    if charge is None or charge.order_id != order.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Postage charge not found on this order")
    if charge.sequence < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The first shipping label is the original shipment and can't be linked to a replacement parcel",
        )
    return charge


async def create_manual_parcel(session: AsyncSession, order: Order, payload: ReplacementParcelCreate) -> OrderReplacementParcel:
    """Records a parcel that has already gone out. Product stock and packaging material
    both leave immediately — there is no reservation step, so the only bound is free
    stock: what's on hand minus what other orders have allocated. Eating into another
    order's reservation would make that order unshippable, so it's a 400, not a partial
    grant (contrast allocation._allocate_line, which reserves what it can).

    Cost snapshots freeze here for the same reason OrderLine.cost_per_unit_snapshot and
    OrderKittingAllocation.unit_cost_snapshot do: a resend's cost of goods shouldn't
    drift when later purchases move the material average."""
    if order.status == OrderStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot add a parcel to a cancelled order")

    charge = None
    if payload.postage_charge_id is not None:
        charge = await _get_charge_for_link(session, order, payload.postage_charge_id)
        if charge.replacement_parcel_id is not None:
            # A label already held by the sync's empty placeholder parcel is the normal
            # "complete this parcel" path: the placeholder exists only to carry the label
            # until the user says what went out, so recording the real parcel against
            # that label replaces it in the same transaction (alert resolved by
            # delete_parcel). A label on a parcel with actual items is a genuine clash.
            holder = (
                await session.execute(
                    select(OrderReplacementParcel)
                    .where(OrderReplacementParcel.id == charge.replacement_parcel_id)
                    .options(selectinload(OrderReplacementParcel.items))
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if holder is None or holder.source != ReplacementParcelSource.sync or holder.items:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="That shipping label is already linked to another parcel",
                )
            await delete_parcel(session, holder)

    parcel = OrderReplacementParcel(
        order_id=order.id,
        reason=payload.reason,
        source=ReplacementParcelSource.manual,
        needs_review=False,
        postage_cost=payload.postage_cost,
        tracking_number=payload.tracking_number or None,
        carrier=payload.carrier or None,
        notes=payload.notes or None,
        sent_at=payload.sent_at or datetime.now(timezone.utc),
    )
    session.add(parcel)
    await session.flush()

    for item in payload.items:
        if item.material_id is not None:
            await _consume_material(session, order, parcel, item.material_id, Decimal(item.qty))
        else:
            await _consume_product(session, order, parcel, item.product_id, item.variant_id, int(item.qty))

    if charge is not None:
        charge.replacement_parcel_id = parcel.id
    await session.flush()
    return parcel


async def _resolve_owner(
    session: AsyncSession, product_id: int | None, variant_id: int | None
) -> tuple[Product | ProductVariant, int, int | None]:
    """(stock owner, product_id, variant_id) — the product is derived from the variant when
    only variant_id was given, same as create_order does for OrderLineInput."""
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        if variant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Variant {variant_id} not found")
        if product_id is not None and variant.product_id != product_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="variant_id does not belong to product_id")
        return variant, variant.product_id, variant.id
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product {product_id} not found")
    return product, product.id, None


async def _consume_product(
    session: AsyncSession,
    order: Order,
    parcel: OrderReplacementParcel,
    product_id: int | None,
    variant_id: int | None,
    qty: int,
) -> None:
    owner, product_id, variant_id = await _resolve_owner(session, product_id, variant_id)
    free = owner.current_stock - owner.allocated_qty
    if qty > free:
        name = getattr(owner, "variant_name", None) or getattr(owner, "name", "product")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot send {qty} × {name} — only {free} free (the rest is allocated to other orders)",
        )
    owner.current_stock -= qty
    record_stock_event(
        session,
        product_id=product_id,
        variant_id=variant_id,
        event_type=ProductStockEventType.replacement_parcel,
        qty_delta=-qty,
        running_balance=owner.current_stock,
        reason=f"Order #{order.id} replacement parcel #{parcel.id}",
        source_replacement_parcel_id=parcel.id,
    )
    # Unlike ship_line, free stock genuinely dropped here (nothing was reserved first), so
    # max_sellable on the live listings changes and the push has to be queued.
    listing_push.enqueue_for_owner(owner)
    session.add(
        OrderReplacementParcelItem(
            parcel_id=parcel.id,
            product_id=product_id,
            variant_id=variant_id,
            qty=Decimal(qty),
            unit_cost_snapshot=await compute_line_cost_snapshot(session, product_id, variant_id),
        )
    )


async def _consume_material(
    session: AsyncSession, order: Order, parcel: OrderReplacementParcel, material_id: int, qty: Decimal
) -> None:
    material = await session.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Material {material_id} not found")
    # Same bound reconcile_order_kitting applies at ship time: on hand minus what other
    # orders have reserved.
    free = Decimal(material.current_qty) - Decimal(material.allocated_qty)
    if qty > free:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot send {qty} × {material.name} — only {free} on hand and not reserved by other orders",
        )
    # Read before the adjustment for clarity only — recompute_material derives
    # avg_unit_cost from purchases alone, so a negative adjustment can't move it.
    unit_cost = Decimal(material.avg_unit_cost)
    session.add(
        MaterialAdjustment(
            material_id=material_id,
            qty_delta=-qty,
            reason=f"Order #{order.id} replacement parcel #{parcel.id}",
            order_id=order.id,
        )
    )
    await recompute_material(session, material_id)
    session.add(
        OrderReplacementParcelItem(parcel_id=parcel.id, material_id=material_id, qty=qty, unit_cost_snapshot=unit_cost)
    )


async def update_parcel(session: AsyncSession, parcel: OrderReplacementParcel, payload: ReplacementParcelUpdate) -> None:
    """Metadata only. Fields absent from the body are left alone (model_fields_set), so a
    PATCH can clear postage_charge_id to None to unlink a label."""
    sent = payload.model_fields_set
    if "reason" in sent and payload.reason is not None:
        parcel.reason = payload.reason
    if "postage_cost" in sent:
        parcel.postage_cost = payload.postage_cost
    if "tracking_number" in sent:
        parcel.tracking_number = payload.tracking_number or None
    if "carrier" in sent:
        parcel.carrier = payload.carrier or None
    if "notes" in sent:
        parcel.notes = payload.notes or None
    if "sent_at" in sent and payload.sent_at is not None:
        parcel.sent_at = payload.sent_at
    if "postage_charge_id" in sent:
        order = await session.get(Order, parcel.order_id)
        current = (await session.execute(
            select(OrderPostageCharge).where(OrderPostageCharge.replacement_parcel_id == parcel.id)
        )).scalar_one_or_none()
        if payload.postage_charge_id is None:
            if current is not None:
                current.replacement_parcel_id = None
        elif current is None or current.id != payload.postage_charge_id:
            charge = await _get_charge_for_link(session, order, payload.postage_charge_id)
            if charge.replacement_parcel_id is not None and charge.replacement_parcel_id != parcel.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="That shipping label is already linked to another parcel"
                )
            if current is not None:
                current.replacement_parcel_id = None
            charge.replacement_parcel_id = parcel.id
    if "needs_review" in sent and payload.needs_review is not None:
        was_pending = parcel.needs_review
        parcel.needs_review = payload.needs_review
        if was_pending and not parcel.needs_review:
            await _resolve_review_alert_if_clear(session, parcel.order_id, excluding=parcel.id)
    await session.flush()


async def delete_parcel(session: AsyncSession, parcel: OrderReplacementParcel) -> None:
    """The exact inverse of create_manual_parcel: every product item goes back onto
    current_stock (with its own reversal event in the Stock history), every material item
    is re-added by a positive adjustment. The linked label, if any, is only unlinked —
    the charge row is marketplace truth and keeps counting against the order's postage
    (a sync-created parcel with no items therefore has nothing to restock; deleting it
    just hides the prompt while the money stays accounted for)."""
    order_id = parcel.order_id
    for item in list(parcel.items):
        if item.material_id is not None:
            session.add(
                MaterialAdjustment(
                    material_id=item.material_id,
                    qty_delta=Decimal(item.qty),
                    reason=f"Order #{order_id} replacement parcel #{parcel.id} deleted — restocked",
                    order_id=order_id,
                )
            )
            await recompute_material(session, item.material_id)
        else:
            owner, product_id, variant_id = await _resolve_owner(session, item.product_id, item.variant_id)
            qty = int(Decimal(item.qty))
            owner.current_stock += qty
            record_stock_event(
                session,
                product_id=product_id,
                variant_id=variant_id,
                event_type=ProductStockEventType.replacement_parcel_reversal,
                qty_delta=qty,
                running_balance=owner.current_stock,
                reason=f"Order #{order_id} replacement parcel #{parcel.id} deleted — restocked",
                source_replacement_parcel_id=parcel.id,
            )
            listing_push.enqueue_for_owner(owner)

    charges = list(
        (await session.execute(select(OrderPostageCharge).where(OrderPostageCharge.replacement_parcel_id == parcel.id))).scalars()
    )
    for charge in charges:
        charge.replacement_parcel_id = None

    was_pending = parcel.needs_review
    await session.delete(parcel)
    await session.flush()
    if was_pending:
        await _resolve_review_alert_if_clear(session, order_id)


async def _resolve_review_alert_if_clear(session: AsyncSession, order_id: int, excluding: int | None = None) -> None:
    """Marks the order's review alert read once no parcel on it still needs review — two
    labels on one order raise two alerts, and the second one shouldn't vanish because the
    first parcel was completed."""
    query = select(OrderReplacementParcel.id).where(
        OrderReplacementParcel.order_id == order_id, OrderReplacementParcel.needs_review.is_(True)
    )
    if excluding is not None:
        query = query.where(OrderReplacementParcel.id != excluding)
    if (await session.execute(query.limit(1))).first() is not None:
        return
    await resolve_alerts(
        session,
        category=NotificationCategory.replacement_parcel_review,
        related_entity_type="order",
        related_entity_id=order_id,
    )


# --- Sync ---------------------------------------------------------------------------------


_SOURCE_BY_PLATFORM = {
    ListingPlatform.ebay: PostageChargeSource.ebay_shipping_label,
    ListingPlatform.etsy: PostageChargeSource.etsy_ledger,
}


def _parse_amount(raw: str) -> Decimal | None:
    try:
        value = abs(Decimal(raw))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return value if value > 0 else None


async def apply_postage_charges(
    session: AsyncSession, order: Order, charges: list[ExternalPostageCharge]
) -> list[PendingReviewAlert]:
    """Upserts the labels a sync reported for `order` and turns every label after the
    first into a replacement parcel.

    Only called when the adapter actually fetched financials (order_sync checks
    financials_enriched) — an empty list on an un-enriched pass means nothing. Rows
    already stored are matched on (platform, external_id) and left alone, with one
    exception: a stored amount is cleared when the marketplace now reports the label as
    a bulk purchase (ExternalPostageCharge.amount None). That's the repair path for
    labels recorded before bulk purchases were recognised — the stored figure was the
    batch total, never this order's cost — and it only ever runs in that direction. Rows
    the marketplace no longer returns are NOT deleted (a narrower fetch window is not a
    refund). New rows are numbered sequence = max + 1 in posted_at order, and that number
    never changes afterwards — see OrderPostageCharge.

    For each new label with sequence >= 2: the oldest parcel on the order with no label
    yet (the user recorded the resend before the sync caught up) gets it; otherwise a
    needs_review parcel is created with the label's cost (or none, for a bulk label —
    the user types what it cost when completing the parcel).

    Returns the review alerts to raise for the parcels it created, rather than raising
    them itself: dispatch_notification commits, and order_sync's write phase is meant to
    be one transaction — the caller sends them once its own commit has landed (see
    raise_pending_review_alerts)."""
    pending: list[PendingReviewAlert] = []
    if order.platform is None or not charges:
        return pending
    source = _SOURCE_BY_PLATFORM.get(order.platform)
    if source is None:
        return pending

    existing = list(
        (await session.execute(select(OrderPostageCharge).where(OrderPostageCharge.order_id == order.id))).scalars()
    )
    known = {c.external_id: c for c in existing}
    next_sequence = max((c.sequence for c in existing), default=0) + 1

    for ext in charges:
        stored = known.get(ext.external_id)
        if stored is not None and ext.amount is None and stored.amount is not None:
            stored.amount = None
            stored.description = ext.description
    fresh = [c for c in charges if c.external_id not in known]
    if not fresh:
        return pending
    # Oldest first; None dates sort last so an undated label never displaces the original.
    fresh.sort(key=lambda c: (c.posted_at is None, c.posted_at or datetime.min.replace(tzinfo=timezone.utc), c.external_id))

    for ext in fresh:
        if ext.amount is None:
            amount = None
        else:
            amount = _parse_amount(ext.amount)
            if amount is None:
                continue
        charge = OrderPostageCharge(
            order_id=order.id,
            platform=order.platform,
            source=source,
            external_id=ext.external_id,
            amount=amount,
            currency=ext.currency,
            posted_at=ext.posted_at,
            description=ext.description,
            sequence=next_sequence,
        )
        next_sequence += 1
        session.add(charge)
        await session.flush()
        if charge.sequence < 2:
            continue

        unlinked = (
            await session.execute(
                select(OrderReplacementParcel)
                .outerjoin(OrderPostageCharge, OrderPostageCharge.replacement_parcel_id == OrderReplacementParcel.id)
                .where(OrderReplacementParcel.order_id == order.id, OrderPostageCharge.id.is_(None))
                .order_by(OrderReplacementParcel.sent_at, OrderReplacementParcel.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if unlinked is not None:
            charge.replacement_parcel_id = unlinked.id
            continue

        parcel = OrderReplacementParcel(
            order_id=order.id,
            reason=ReplacementParcelReason.unspecified,
            source=ReplacementParcelSource.sync,
            needs_review=True,
            sent_at=ext.posted_at or datetime.now(timezone.utc),
            notes=f"Auto-created from {order.platform.value} shipping label {ext.external_id}",
        )
        session.add(parcel)
        await session.flush()
        charge.replacement_parcel_id = parcel.id
        pending.append(
            PendingReviewAlert(
                order_id=order.id,
                external_order_id=order.external_order_id,
                platform=order.platform,
                amount=amount,
                currency=ext.currency,
                posted_at=ext.posted_at,
            )
        )
    return pending


async def raise_pending_review_alerts(session: AsyncSession, pending: list[PendingReviewAlert]) -> None:
    for alert in pending:
        await raise_replacement_parcel_review_alert(session, alert)
