from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialAdjustment
from app.models.order import Order, OrderLine, OrderStatus
from app.models.order_return import OrderLineReturn, ReturnDisposition, ReturnScope, ReturnSource
from app.models.product import Product
from app.models.stock_adjustment import StockAdjustment, StockAdjustmentMode
from app.models.variant import ProductVariant
from app.schemas.order_return import (
    CancellationKittingMaterial,
    CancellationLineOption,
    CancellationPreview,
    LineCancellationDecision,
)
from app.services import allocation, listing_push
from app.services.costing import recompute_material
from app.services.kitting import get_resolved_kitting_bom

"""Cancellation and return handling — the per-line scrap/return-to-stock workflow from
docs/plan-marketplace-integrations.md Section 4. Supersedes allocation.cancel_order,
which only ever handled the not-yet-shipped, everything-returns-to-stock case and
hard-blocked (409) anything with shipped units. See OrderLineReturn's own docstring for
what each (scope, source, disposition) combination actually does to stock."""


async def _get_lines(session: AsyncSession, order_id: int) -> list[OrderLine]:
    result = await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))
    return list(result.scalars())


async def _get_stock_owner(session: AsyncSession, line: OrderLine) -> Product | ProductVariant | None:
    if line.variant_id is not None:
        return await session.get(ProductVariant, line.variant_id)
    if line.product_id is not None:
        return await session.get(Product, line.product_id)
    return None


async def _resolve_names(session: AsyncSession, product_id: int | None, variant_id: int | None) -> tuple[str | None, str | None]:
    product_name = variant_name = None
    if product_id is not None:
        product = await session.get(Product, product_id)
        product_name = product.name if product else None
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        variant_name = variant.variant_name if variant else None
    return product_name, variant_name


async def get_cancellation_preview(session: AsyncSession, order: Order) -> CancellationPreview:
    lines = await _get_lines(session, order.id)
    options: list[CancellationLineOption] = []

    for line in lines:
        pending_qty = max(line.allocated_qty - line.shipped_qty, 0)
        shipped_qty = line.shipped_qty
        if pending_qty <= 0 and shipped_qty <= 0:
            continue  # nothing left to decide for this line

        product_name, variant_name = await _resolve_names(session, line.product_id, line.variant_id)

        kitting_materials: list[CancellationKittingMaterial] = []
        if shipped_qty > 0 and line.product_id is not None:
            bom = await get_resolved_kitting_bom(session, line.product_id, line.variant_id)
            material_ids = [b.material_id for b in bom]
            materials = (
                {
                    m.id: m
                    for m in (await session.execute(select(Material).where(Material.id.in_(material_ids)))).scalars()
                }
                if material_ids
                else {}
            )
            kitting_materials = [
                CancellationKittingMaterial(
                    material_id=b.material_id,
                    material_name=materials[b.material_id].name if b.material_id in materials else "Unknown material",
                    qty_per_unit=b.qty_required,
                )
                for b in bom
            ]

        options.append(
            CancellationLineOption(
                order_line_id=line.id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                product_name=product_name,
                variant_name=variant_name,
                pending_qty=pending_qty,
                shipped_qty=shipped_qty,
                default_product_disposition=ReturnDisposition.return_to_stock,
                kitting_materials=kitting_materials,
                default_kitting_disposition=ReturnDisposition.scrap,
            )
        )

    return CancellationPreview(
        order_id=order.id, already_cancelled=order.status == OrderStatus.cancelled, lines=options
    )


async def _adjust_product_stock(
    session: AsyncSession, owner: Product | ProductVariant | None, line: OrderLine, qty_delta: int, reason: str
) -> None:
    if owner is None or qty_delta == 0:
        return
    owner.current_stock += qty_delta
    session.add(
        StockAdjustment(
            product_id=line.product_id,
            variant_id=line.variant_id,
            mode=StockAdjustmentMode.adjust,
            qty_delta=qty_delta,
            reason=reason,
        )
    )
    listing_push.enqueue_for_owner(owner)


async def process_cancellation(
    session: AsyncSession, order: Order, decisions: list[LineCancellationDecision], reason: str | None
) -> Order:
    if order.status == OrderStatus.cancelled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order is already cancelled")

    lines = await _get_lines(session, order.id)
    decisions_by_line = {d.order_line_id: d for d in decisions}

    for line in lines:
        pending_qty = max(line.allocated_qty - line.shipped_qty, 0)
        needs_decision = pending_qty > 0 or line.shipped_qty > 0
        if not needs_decision:
            continue
        decision = decisions_by_line.get(line.id)
        if decision is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Missing disposition decision for line {line.id}"
            )
        if line.shipped_qty > 0 and decision.kitting_disposition is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Line {line.id} has shipped units — kitting_disposition is required",
            )

        owner = await _get_stock_owner(session, line)

        if pending_qty > 0:
            # Releasing the reservation is not optional — it's the baseline both
            # dispositions build on (see OrderLineReturn's docstring). Reuses
            # allocation.deallocate_line rather than reimplementing it; the extra
            # recompute/reconcile work it does per-call is redundant (order.status gets
            # overwritten below regardless) but harmless.
            await allocation.deallocate_line(session, line, pending_qty)
            if decision.product_disposition == ReturnDisposition.scrap:
                await _adjust_product_stock(
                    session, owner, line, -pending_qty, f"Order #{order.id} cancelled — unshipped units scrapped"
                )
            session.add(
                OrderLineReturn(
                    order_line_id=line.id,
                    scope=ReturnScope.product,
                    qty=pending_qty,
                    disposition=decision.product_disposition,
                    source=ReturnSource.cancel_before_ship,
                    reason=reason,
                )
            )

        if line.shipped_qty > 0:
            if decision.product_disposition == ReturnDisposition.return_to_stock:
                await _adjust_product_stock(
                    session, owner, line, line.shipped_qty, f"Order #{order.id} returned — unit returned to stock"
                )
            session.add(
                OrderLineReturn(
                    order_line_id=line.id,
                    scope=ReturnScope.product,
                    qty=line.shipped_qty,
                    disposition=decision.product_disposition,
                    source=ReturnSource.return_after_ship,
                    reason=reason,
                )
            )

            if line.product_id is not None:
                bom = await get_resolved_kitting_bom(session, line.product_id, line.variant_id)
                for bom_line in bom:
                    material_qty = bom_line.qty_required * line.shipped_qty
                    if decision.kitting_disposition == ReturnDisposition.return_to_stock:
                        session.add(
                            MaterialAdjustment(
                                material_id=bom_line.material_id,
                                qty_delta=material_qty,
                                reason=f"Order #{order.id} returned — kitting material returned to stock",
                                order_id=order.id,
                            )
                        )
                        await recompute_material(session, bom_line.material_id)
                    session.add(
                        OrderLineReturn(
                            order_line_id=line.id,
                            scope=ReturnScope.kitting,
                            material_id=bom_line.material_id,
                            qty=material_qty,
                            disposition=decision.kitting_disposition,
                            source=ReturnSource.return_after_ship,
                            reason=reason,
                        )
                    )

    order.status = OrderStatus.cancelled
    order.cancelled_at = datetime.now(timezone.utc)
    order.sync_issue = None
    order.pending_marketplace_cancellation = False
    # Deliberately no explicit reconcile_order_kitting(order) call here, unlike the old
    # allocation.cancel_order. allocation.deallocate_line (called above, when there's
    # pending qty to release) already reconciles internally — that's the only case where
    # this order's reserved/consumed *targets* actually changed. Calling it again
    # unconditionally for the whole order is not just redundant: on a shipped line whose
    # OrderKittingAllocation ledger has drifted from reality (confirmed to happen on real
    # data — a seeded/migrated order with no ledger row at all), it recomputes
    # consumed_target from shipped_qty and treats the "missing" consumption as new,
    # double-charging the material with a second MaterialAdjustment for stock that was
    # already consumed. The scrap/return-to-stock adjustments above are the only stock
    # effects a cancel/return should ever cause beyond releasing a reservation.
    return order
