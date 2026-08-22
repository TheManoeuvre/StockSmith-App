from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.kitting import OrderKittingOverride
from app.models.listing import ListingPlatform
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.sku_alias import SkuAlias
from app.models.variant import ProductVariant
from app.schemas.kitting import OrderKittingOverrideLine, OrderKittingSummary
from app.schemas.order import (
    CreateProductAndMapRequest,
    DeallocateRequest,
    MapSkuRequest,
    OrderCreate,
    OrderLineQtyUpdate,
    OrderLineRead,
    OrderPage,
    OrderRead,
    OrderUpdate,
)
from app.schemas.order_return import CancellationPreview, OrderCancelRequest
from app.services import allocation, returns
from app.services.kitting import (
    apply_default_kitting_bom,
    get_kitting_cogs_by_order,
    get_order_kitting_summary,
    reconcile_order_kitting,
)
from app.services.variants import compute_full_sku

router = APIRouter(prefix="/orders", tags=["orders"], dependencies=[Depends(require_auth)])


async def _get_order_with_lines(session: AsyncSession, order_id: int) -> Order:
    # populate_existing forces a fresh load even if this Order is already in the session's
    # identity map with stale lines — several endpoints below fetch the same order twice
    # in one request (once before mutating, once after) and would otherwise see cached data.
    result = await session.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.lines).selectinload(OrderLine.product),
            selectinload(Order.lines).selectinload(OrderLine.variant),
            selectinload(Order.shipping_profile),
        )
        .execution_options(populate_existing=True)
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


async def _get_line(session: AsyncSession, line_id: int) -> OrderLine:
    line = await session.get(OrderLine, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order line not found")
    return line


async def _remember_sku(
    session: AsyncSession,
    platform: ListingPlatform,
    external_sku: str,
    product_id: int,
    variant_id: int | None,
) -> None:
    """Backfills the product's own SKU if it doesn't have one yet, or — if it already has
    a different SKU of its own — remembers external_sku as a SkuAlias so future orders
    match automatically without this same line needing to be mapped by hand again."""
    product = await session.get(Product, product_id)
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        current_full_sku = compute_full_sku(product.sku, variant.sku_suffix)
    else:
        current_full_sku = product.sku

    if not current_full_sku:
        if not product.sku:
            product.sku = external_sku
        return

    if current_full_sku == external_sku:
        return

    existing = await session.execute(
        select(SkuAlias.id).where(SkuAlias.platform == platform, SkuAlias.external_sku == external_sku)
    )
    if existing.scalar_one_or_none() is None:
        session.add(SkuAlias(platform=platform, external_sku=external_sku, product_id=product_id, variant_id=variant_id))


def _materials_cogs(order: Order) -> Decimal | None:
    """The build-BOM half of cost of goods: each line's snapshotted cost per unit across its
    shipped_qty. Packaging is deliberately NOT here — it's consumed per order, not per unit
    (see kitting.get_kitting_cogs_by_order).

    Returns None rather than 0 when no shipped line carries a snapshot at all, so a caller
    can render "—" instead of asserting the goods were free."""
    total = Decimal(0)
    found = False
    for line in order.lines:
        if line.shipped_qty <= 0 or line.cost_per_unit_snapshot is None:
            continue
        found = True
        total += Decimal(line.cost_per_unit_snapshot) * line.shipped_qty
    return total if found else None


def _compute_net_profit(
    order: Order, materials_cogs: Decimal | None, kitting_cogs: Decimal | None
) -> Decimal | None:
    """Order Value Paid + Postage Paid - Platform Fees - Postage cost - Cost of Goods.

    Order Value Paid (subtotal) and Postage Paid (shipping_charged) are what the buyer
    paid, straight off the marketplace receipt (or, for a manual order, derived from its
    own lines — see create_order/_recompute_manual_order_totals). Platform Fees
    (payment_fees) is the full marketplace deduction — for Etsy, aggregated from the
    payment-account ledger once the order has shipped (see platforms/etsy.py
    _fetch_platform_fees_total), since the per-receipt Payments endpoint only reports
    the card-processing portion of it. Postage cost (shipping_cost_snapshot) is the
    seller's own cost for that shipping method, frozen at ship time — Etsy doesn't
    expose actual per-order postage cost anywhere reliably attributable, so this always
    comes from the assigned Shipping Profile, never from synced marketplace data.

    Cost of Goods arrives in two halves, computed elsewhere and passed in, because they're
    sourced and frozen differently. materials_cogs (_materials_cogs) is per line: the
    build-BOM cost per unit across shipped_qty, frozen at first allocation (see
    allocation._allocate_line). kitting_cogs (kitting.get_kitting_cogs_by_order) is per
    ORDER: what the kitting ledger actually consumed, valued at the cost frozen when it was
    consumed. Packaging can't be a per-unit rate — one box ships three units, and
    auto_apply_multiunit_kitting_override encodes exactly that. Both halves count only
    shipped units: cost isn't real until they've left the building.

    Returns None rather than a misleading number when Order Value Paid isn't known yet
    (a marketplace order whose financials haven't synced, or a manual order created
    before this field was tracked)."""
    if order.subtotal is None:
        return None

    revenue = Decimal(order.subtotal) + Decimal(order.shipping_charged or 0) - Decimal(order.refunded_amount or 0)
    platform_fees = Decimal(order.payment_fees or 0)
    postage_cost = Decimal(order.shipping_cost_snapshot or 0)
    cogs = (materials_cogs or Decimal(0)) + (kitting_cogs or Decimal(0))

    return revenue - platform_fees - postage_cost - cogs


def _cogs_pending(order: Order) -> bool:
    """True when at least one line that should have a cost by now (it's been allocated,
    or shipped, or is simply awaiting its first allocation) still has no snapshot — the
    signal a UI should show as "COGS pending" rather than silently reading net_profit as
    though that line cost nothing.

    Materials only, deliberately. Kitting has no per-line snapshot to be missing; a shipped
    order whose kitting ledger drifted is a data-repair problem
    (scripts/backfill_order_kitting_ledger.py), not something to flag on every read."""
    return any(
        not line.needs_mapping and line.product_id is not None and line.cost_per_unit_snapshot is None
        for line in order.lines
    )


def _postage_cost_missing(order: Order) -> bool:
    """True when an order has shipped without ever recording what the postage cost — the
    signal a UI should show instead of letting net_profit read as though postage were free.

    Deliberately NOT folded into cogs_pending. That flag means "a line hasn't been allocated
    yet", says so in its copy, and is fixed by allocating the line; this one is fixed by
    assigning the product a shipping profile. Merging them would make both messages wrong
    half the time.

    The shipped gate is the whole rule: shipping_cost_snapshot is legitimately NULL on every
    order that hasn't shipped, since ship_order is what freezes it. Only once the units have
    physically left the building does a missing figure mean a real cost went unrecorded.

    Should be permanently empty for orders placed after the fixes in
    order_costs.default_order_shipping_profile landed, which makes it a standing regression
    check on that path rather than only a disclaimer about historical rows."""
    return order.status == OrderStatus.shipped and order.shipping_cost_snapshot is None


async def _recompute_manual_order_totals(session: AsyncSession, order: Order) -> None:
    """Manual orders have no marketplace receipt to source subtotal/grand_total from —
    this derives them from the order's own lines whenever ordered_qty or
    shipping_charged changes, so "Order Value Paid" stays accurate for the net-profit
    breakdown above. A no-op for synced orders (subtotal always comes from the receipt)."""
    if order.platform is not None:
        return
    result = await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))
    lines = list(result.scalars())
    if not any(l.unit_price is not None for l in lines):
        return
    subtotal = sum((Decimal(l.unit_price or 0) * l.ordered_qty for l in lines), Decimal(0))
    order.subtotal = subtotal
    order.grand_total = subtotal + Decimal(order.shipping_charged or 0)


def _serialize_order(order: Order, kitting_cogs: Decimal | None) -> OrderRead:
    """kitting_cogs is passed in rather than computed here because it needs a DB round-trip
    and this stays synchronous — list_orders fetches one aggregate for its whole page. Single
    order? Use _serialize_one."""
    lines = [
        OrderLineRead(
            id=line.id,
            order_id=line.order_id,
            product_id=line.product_id,
            variant_id=line.variant_id,
            product_name=line.product.name if line.product else None,
            variant_name=line.variant.variant_name if line.variant else None,
            sku=line.sku,
            ordered_qty=line.ordered_qty,
            allocated_qty=line.allocated_qty,
            shipped_qty=line.shipped_qty,
            unit_price=line.unit_price,
            currency=line.currency,
            external_line_id=line.external_line_id,
            needs_mapping=line.needs_mapping,
            cost_per_unit_snapshot=line.cost_per_unit_snapshot,
        )
        for line in order.lines
    ]
    materials_cogs = _materials_cogs(order)
    return OrderRead(
        id=order.id,
        platform=order.platform,
        external_order_id=order.external_order_id,
        status=order.status,
        buyer_name=order.buyer_name,
        buyer_note=order.buyer_note,
        order_placed_at=order.order_placed_at,
        shipped_at=order.shipped_at,
        cancelled_at=order.cancelled_at,
        notes=order.notes,
        created_at=order.created_at,
        updated_at=order.updated_at,
        grand_total=order.grand_total,
        subtotal=order.subtotal,
        shipping_charged=order.shipping_charged,
        shipping_profile_id=order.shipping_profile_id,
        shipping_profile_name=order.shipping_profile.name if order.shipping_profile else None,
        shipping_cost_snapshot=order.shipping_cost_snapshot,
        tax_charged=order.tax_charged,
        vat_charged=order.vat_charged,
        discount_amount=order.discount_amount,
        refunded_amount=order.refunded_amount,
        currency=order.currency,
        payment_fees=order.payment_fees,
        payment_net=order.payment_net,
        payment_status=order.payment_status,
        financials_synced_at=order.financials_synced_at,
        materials_cogs=materials_cogs,
        kitting_cogs=kitting_cogs,
        net_profit=_compute_net_profit(order, materials_cogs, kitting_cogs),
        cogs_pending=_cogs_pending(order),
        postage_cost_missing=_postage_cost_missing(order),
        sync_issue=order.sync_issue,
        pending_marketplace_cancellation=order.pending_marketplace_cancellation,
        lines=lines,
    )


async def _serialize_one(session: AsyncSession, order: Order) -> OrderRead:
    """_serialize_order for the single-order endpoints, fetching that order's kitting COGS."""
    kitting = await get_kitting_cogs_by_order(session, [order.id])
    return _serialize_order(order, kitting.get(order.id))


@router.get("", response_model=OrderPage)
async def list_orders(
    status_filter: OrderStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> OrderPage:
    count_query = select(func.count()).select_from(Order)
    query = (
        select(Order)
        .options(
            selectinload(Order.lines).selectinload(OrderLine.product),
            selectinload(Order.lines).selectinload(OrderLine.variant),
            selectinload(Order.shipping_profile),
        )
        .order_by(Order.order_placed_at.desc(), Order.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status_filter is not None:
        count_query = count_query.where(Order.status == status_filter)
        query = query.where(Order.status == status_filter)
    total = await session.scalar(count_query)
    result = await session.execute(query)
    orders = list(result.scalars())
    # One aggregate for the whole page rather than one per order — this endpoint serves up
    # to 200 at a time. See get_kitting_cogs_by_order.
    kitting_by_order = await get_kitting_cogs_by_order(session, [o.id for o in orders])
    return OrderPage(
        items=[_serialize_order(o, kitting_by_order.get(o.id)) for o in orders],
        total=total or 0,
    )


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, session: AsyncSession = Depends(get_db)) -> OrderRead:
    if not payload.lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="An order needs at least one line")

    for line in payload.lines:
        if line.variant_id is not None:
            variant = await session.get(ProductVariant, line.variant_id)
            if variant is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Variant {line.variant_id} not found"
                )
        else:
            product = await session.get(Product, line.product_id)
            if product is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Product {line.product_id} not found"
                )

    if payload.shipping_profile_id is not None:
        profile = await session.get(ShippingProfile, payload.shipping_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Shipping profile {payload.shipping_profile_id} not found"
            )
        shipping_charged = payload.shipping_charged if payload.shipping_charged is not None else profile.price
    else:
        profile = None
        shipping_charged = payload.shipping_charged

    has_any_price = any(l.unit_price is not None for l in payload.lines)
    subtotal = (
        sum((Decimal(l.unit_price or 0) * l.ordered_qty for l in payload.lines), Decimal(0)) if has_any_price else None
    )
    grand_total = subtotal + Decimal(shipping_charged or 0) if subtotal is not None else None

    order = Order(
        buyer_name=payload.buyer_name,
        buyer_note=payload.buyer_note,
        notes=payload.notes,
        currency=payload.currency,
        shipping_profile_id=profile.id if profile else None,
        shipping_charged=shipping_charged,
        subtotal=subtotal,
        grand_total=grand_total,
    )
    order.lines = []
    for l in payload.lines:
        # A variant-only line needs its product_id derived from the variant so
        # product-based lookups elsewhere have something to resolve against. Cost
        # snapshots are deliberately left unset here — captured at first allocation
        # instead (allocation._allocate_line), not at creation.
        product_id = l.product_id
        if l.variant_id is not None:
            variant = await session.get(ProductVariant, l.variant_id)
            product_id = variant.product_id

        order.lines.append(
            OrderLine(
                product_id=product_id,
                variant_id=l.variant_id,
                ordered_qty=l.ordered_qty,
                unit_price=l.unit_price,
                currency=l.currency,
            )
        )
    session.add(order)
    await session.flush()

    await allocation.allocate_order(session, order, source="order-create")

    await session.commit()
    order = await _get_order_with_lines(session, order.id)
    return await _serialize_one(session, order)


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(order_id: int, session: AsyncSession = Depends(get_db)) -> OrderRead:
    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


@router.patch("/{order_id}", response_model=OrderRead)
async def update_order(order_id: int, payload: OrderUpdate, session: AsyncSession = Depends(get_db)) -> OrderRead:
    order = await _get_order_with_lines(session, order_id)
    changed_fields = set(payload.model_dump(exclude_unset=True).keys())

    if order.status == OrderStatus.shipped and changed_fields & {"shipping_profile_id", "shipping_charged"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change shipping once the order has shipped — its cost is already frozen",
        )

    if "shipping_profile_id" in changed_fields and payload.shipping_profile_id is not None:
        profile = await session.get(ShippingProfile, payload.shipping_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Shipping profile {payload.shipping_profile_id} not found"
            )

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(order, field, value)

    if "shipping_charged" in changed_fields:
        await _recompute_manual_order_totals(session, order)

    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(order_id: int, session: AsyncSession = Depends(get_db)) -> None:
    order = await _get_order_with_lines(session, order_id)
    for line in order.lines:
        if line.allocated_qty > 0 or line.shipped_qty > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete an order with allocated or shipped units — cancel it first",
            )
    await session.delete(order)
    await session.commit()


@router.get("/{order_id}/cancellation-preview", response_model=CancellationPreview)
async def cancellation_preview(order_id: int, session: AsyncSession = Depends(get_db)) -> CancellationPreview:
    """Per-line breakdown of what a cancel/return would do — pending (allocated but
    unshipped) qty, shipped qty, and (for shipped lines) the resolved kitting materials
    involved — each with its default disposition, for the frontend to show a real
    scrap/return-to-stock prompt instead of asking blind. See services/returns.py."""
    order = await _get_order_with_lines(session, order_id)
    return await returns.get_cancellation_preview(session, order)


@router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order_endpoint(
    order_id: int, payload: OrderCancelRequest, session: AsyncSession = Depends(get_db)
) -> OrderRead:
    order = await _get_order_with_lines(session, order_id)
    await returns.process_cancellation(session, order, payload.line_decisions, payload.reason)
    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


@router.post("/{order_id}/ship", response_model=OrderRead)
async def ship_order_endpoint(order_id: int, session: AsyncSession = Depends(get_db)) -> OrderRead:
    order = await _get_order_with_lines(session, order_id)
    await allocation.ship_order(session, order)
    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


@router.post("/{order_id}/allocate", response_model=OrderRead)
async def allocate_order_endpoint(order_id: int, session: AsyncSession = Depends(get_db)) -> OrderRead:
    """Re-runs allocation against current free stock — the only way to grant newly
    available stock to an order that's already past creation (a manual stock
    adjustment, or a sync that flagged sync_issue for lack of allocation, don't
    retrigger allocation on their own). Safe to call repeatedly: allocate_order only
    ever tops lines up toward ordered_qty and is a no-op once nothing's left to grant."""
    order = await _get_order_with_lines(session, order_id)
    if order.status in (OrderStatus.shipped, OrderStatus.cancelled):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot allocate a {order.status.value} order"
        )
    await allocation.allocate_order(session, order, source="manual")
    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, order_id))


@router.patch("/lines/{line_id}", response_model=OrderRead)
async def update_line_qty(
    line_id: int, payload: OrderLineQtyUpdate, session: AsyncSession = Depends(get_db)
) -> OrderRead:
    line = await _get_line(session, line_id)
    await allocation.apply_ordered_qty_change(session, line, payload.ordered_qty)
    order = await session.get(Order, line.order_id)
    if order is not None:
        await _recompute_manual_order_totals(session, order)
    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, line.order_id))


@router.post("/lines/{line_id}/unassign", response_model=OrderRead)
async def unassign_line(
    line_id: int, payload: DeallocateRequest, session: AsyncSession = Depends(get_db)
) -> OrderRead:
    line = await _get_line(session, line_id)
    await allocation.deallocate_line(session, line, payload.qty)
    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, line.order_id))


@router.post("/lines/{line_id}/map-sku", response_model=OrderRead)
async def map_sku(line_id: int, payload: MapSkuRequest, session: AsyncSession = Depends(get_db)) -> OrderRead:
    line = await _get_line(session, line_id)
    if not line.needs_mapping:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Line does not need mapping")

    order = await session.get(Order, line.order_id)

    if payload.variant_id is not None:
        variant = await session.get(ProductVariant, payload.variant_id)
        if variant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
        line.variant_id = payload.variant_id
        line.product_id = variant.product_id
    else:
        product = await session.get(Product, payload.product_id)
        if product is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
        line.product_id = payload.product_id
        line.variant_id = None

    line.needs_mapping = False
    await session.flush()

    if order.platform is not None and line.sku:
        await _remember_sku(session, order.platform, line.sku, line.product_id, line.variant_id)

    await allocation.allocate_order(session, order, source="map-sku")

    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, line.order_id))


@router.post(
    "/lines/{line_id}/create-product-and-map", response_model=OrderRead, status_code=status.HTTP_201_CREATED
)
async def create_product_and_map(
    line_id: int, payload: CreateProductAndMapRequest, session: AsyncSession = Depends(get_db)
) -> OrderRead:
    """Creates a brand-new Product for an unrecognized order line and maps the line to
    it in one step — for when "assign to an existing product" isn't the right fix
    because this SKU genuinely isn't in StockSmith yet."""
    line = await _get_line(session, line_id)
    if not line.needs_mapping:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Line does not need mapping")

    order = await session.get(Order, line.order_id)

    product = Product(name=payload.name)
    session.add(product)
    await session.flush()  # assigns product.id without a second commit round-trip
    product.sku = payload.sku or line.sku or f"SKU-{product.id:04d}"
    await apply_default_kitting_bom(session, product.id)

    line.product_id = product.id
    line.variant_id = None
    line.needs_mapping = False
    await session.flush()

    await allocation.allocate_order(session, order, source="create-product-and-map")

    await session.commit()
    return await _serialize_one(session, await _get_order_with_lines(session, line.order_id))


@router.get("/{order_id}/kitting-overrides", response_model=OrderKittingSummary)
async def get_kitting_overrides(order_id: int, session: AsyncSession = Depends(get_db)) -> OrderKittingSummary:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return await get_order_kitting_summary(session, order_id)


@router.put("/{order_id}/kitting-overrides", response_model=OrderKittingSummary)
async def replace_kitting_overrides(
    order_id: int, payload: list[OrderKittingOverrideLine], session: AsyncSession = Depends(get_db)
) -> OrderKittingSummary:
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    for line in payload:
        if line.material_id == line.replaces_material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Material {line.material_id} cannot substitute itself",
            )

    await session.execute(delete(OrderKittingOverride).where(OrderKittingOverride.order_id == order_id))
    overrides = [
        OrderKittingOverride(
            order_id=order_id,
            material_id=l.material_id,
            qty_required=l.qty_required,
            replaces_material_id=l.replaces_material_id,
        )
        for l in payload
    ]
    session.add_all(overrides)
    await session.flush()

    await reconcile_order_kitting(session, order)
    await session.commit()

    return await get_order_kitting_summary(session, order_id)
