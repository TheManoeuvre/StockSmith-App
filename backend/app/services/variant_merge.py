"""Merging one variant into a sibling — the "we made this twice" fix.

Two variants of one product that are the same thing (a duplicate created by hand, or a
"Standard" size that turned out to be no different from the plain one) each hold their
own stock, their own open orders, their own marketplace variation and their own BOM
overrides. Folding them together is therefore not a row edit but a small choreography,
and this module is that choreography, with the pieces borrowed from the services that
already own each step:

  * open order lines move with services/order_substitution — the same split-and-reallocate
    a customer's change of mind gets, so it is undoable line by line and leaves a
    substitution record rather than a mystery;
  * stock moves as two stock adjustments (services/stock_adjustments), so both variants'
    ledgers show where the units went and the loser's running balance closes at zero;
  * the loser's marketplace variation is pushed to zero before the variant is disabled,
    because a disabled variant is excluded from every later push and would otherwise stay
    on sale at whatever quantity was last sent.

The loser is *deactivated*, never deleted. Orders, builds, parcels and stock events point
at it with RESTRICT or per-owner running balances, and they are the record of what
happened. Its SKU is remembered as an alias for the survivor so a marketplace order that
still quotes it lands in the right place.

Everything is planned first (`plan_merge`, read-only) and applied second, and the apply
re-plans rather than trusting a preview the client may have held for a while.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import ProductAsset
from app.models.kitting import ProductVariantKittingMaterial
from app.models.listing import Listing, ListingPlatform
from app.models.material import Material
from app.models.order import Order, OrderLine
from app.models.platform_listing_push import ListingPushStatus
from app.models.product import Product
from app.models.sku_alias import SkuAlias
from app.models.stock_adjustment import StockAdjustmentMode
from app.models.stock_take import StockTake, StockTakeLine, StockTakeStatus
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.variant import (
    LiveListingResolution,
    MergeBomChoice,
    MergeBomLine,
    MergeLiveListing,
    MergeOpenLine,
    VariantMergePlan,
    VariantMergeUnit,
)
from app.services import allocation, listing_push, order_substitution, stock_adjustments
from app.services.buildability import get_resolved_variant_bom
from app.services.kitting import get_resolved_kitting_bom
from app.services.variants import compute_full_sku


class VariantMergeError(RuntimeError):
    """Refused. The message is user-facing."""


@dataclass
class MergeOutcome:
    survivor: ProductVariant
    stock_moved: int
    open_lines_moved: int
    warnings: list[str]


async def _load_pair(session: AsyncSession, loser_id: int, survivor_id: int) -> tuple[ProductVariant, ProductVariant]:
    if loser_id == survivor_id:
        raise VariantMergeError("Cannot merge a variant into itself.")
    loser = await session.get(ProductVariant, loser_id)
    survivor = await session.get(ProductVariant, survivor_id)
    if loser is None or survivor is None:
        raise VariantMergeError("Variant not found.")
    if loser.product_id != survivor.product_id:
        raise VariantMergeError("Both variants must belong to the same product.")
    if not survivor.is_active:
        raise VariantMergeError(f'"{survivor.variant_name}" is disabled — reactivate it before merging into it.')
    return loser, survivor


async def _open_lines(session: AsyncSession, variant_id: int) -> list[OrderLine]:
    """Lines with units still to ship on orders that are still live. A cancelled order's
    lines are history and stay where they are; a fully shipped line likewise."""
    result = await session.execute(
        select(OrderLine)
        .join(Order, Order.id == OrderLine.order_id)
        .where(
            OrderLine.variant_id == variant_id,
            OrderLine.shipped_qty < OrderLine.ordered_qty,
            Order.cancelled_at.is_(None),
        )
        .order_by(OrderLine.id)
    )
    return list(result.scalars())


async def _live_listings(session: AsyncSession, variant_id: int) -> list[Listing]:
    result = await session.execute(
        select(Listing).where(Listing.variant_id == variant_id, Listing.external_listing_id.is_not(None))
    )
    return list(result.scalars())


async def _open_stock_takes(session: AsyncSession, variant_ids: list[int]) -> list[int]:
    result = await session.execute(
        select(StockTakeLine.variant_id)
        .join(StockTake, StockTake.id == StockTakeLine.stock_take_id)
        .where(StockTake.status == StockTakeStatus.open, StockTakeLine.variant_id.in_(variant_ids))
        .distinct()
    )
    return list(result.scalars())


async def _named_bom(session: AsyncSession, lines, names: dict[int, str]) -> list[MergeBomLine]:
    wanted = {line.material_id for line in lines} | {
        line.replaces_material_id for line in lines if line.replaces_material_id is not None
    }
    missing = wanted - names.keys()
    if missing:
        result = await session.execute(select(Material.id, Material.name).where(Material.id.in_(missing)))
        names.update({row.id: row.name for row in result})
    return sorted(
        (
            MergeBomLine(
                material_id=line.material_id,
                material_name=names.get(line.material_id, f"#{line.material_id}"),
                qty_required=line.qty_required,
                replaces_material_id=line.replaces_material_id,
                replaces_material_name=(
                    names.get(line.replaces_material_id) if line.replaces_material_id is not None else None
                ),
            )
            for line in lines
        ),
        key=lambda l: (l.material_name, l.replaces_material_id or 0),
    )


def _bom_key(lines: list[MergeBomLine]) -> set[tuple[int, int | None, str]]:
    return {(l.material_id, l.replaces_material_id, str(l.qty_required.normalize())) for l in lines}


def _unit(product: Product, variant: ProductVariant) -> VariantMergeUnit:
    return VariantMergeUnit(
        id=variant.id,
        variant_name=variant.variant_name,
        full_sku=compute_full_sku(product.sku, variant.sku_suffix),
        is_active=variant.is_active,
        current_stock=variant.current_stock,
        allocated_qty=variant.allocated_qty,
    )


async def plan_merge(session: AsyncSession, loser_id: int, survivor_id: int) -> VariantMergePlan:
    """What merging `loser_id` into `survivor_id` would do. Reads only."""
    loser, survivor = await _load_pair(session, loser_id, survivor_id)
    product = await session.get(Product, loser.product_id)
    product_id = loser.product_id

    names: dict[int, str] = {}
    loser_bom = await _named_bom(session, await get_resolved_variant_bom(session, product_id, loser.id), names)
    survivor_bom = await _named_bom(session, await get_resolved_variant_bom(session, product_id, survivor.id), names)
    loser_kitting = await _named_bom(session, await get_resolved_kitting_bom(session, product_id, loser.id), names)
    survivor_kitting = await _named_bom(
        session, await get_resolved_kitting_bom(session, product_id, survivor.id), names
    )

    open_lines = await _open_lines(session, loser.id)
    orders = {
        o.id: o
        for o in (
            await session.execute(select(Order).where(Order.id.in_({l.order_id for l in open_lines})))
        ).scalars()
    } if open_lines else {}

    blockers: list[str] = []
    in_open_take = await _open_stock_takes(session, [loser.id, survivor.id])
    if in_open_take:
        blockers.append(
            "One of these variants is on an open stock take. Approve or cancel the count before merging."
        )

    return VariantMergePlan(
        loser=_unit(product, loser),
        survivor=_unit(product, survivor),
        stock_to_move=loser.current_stock,
        open_lines=[
            MergeOpenLine(
                order_id=l.order_id,
                order_reference=orders[l.order_id].external_order_id if l.order_id in orders else None,
                qty=l.ordered_qty - l.shipped_qty,
            )
            for l in open_lines
        ],
        bom_differs=_bom_key(loser_bom) != _bom_key(survivor_bom),
        kitting_differs=_bom_key(loser_kitting) != _bom_key(survivor_kitting),
        loser_bom=loser_bom,
        survivor_bom=survivor_bom,
        loser_kitting=loser_kitting,
        survivor_kitting=survivor_kitting,
        live_listings=[
            MergeLiveListing(
                platform=l.platform.value, published_sku=l.published_sku, external_listing_id=l.external_listing_id
            )
            for l in await _live_listings(session, loser.id)
        ],
        blockers=blockers,
    )


def require_no_live_listing_conflicts(plan: VariantMergePlan, resolution: LiveListingResolution) -> None:
    """The confirmable refusal. Same detail shape as variant_platform_conflicts so the
    client renders it with the dialog it already has."""
    if resolution == "proceed" or not plan.live_listings:
        return
    conflicts = [
        {
            "platform": l.platform,
            "published_sku": l.published_sku,
            "external_listing_id": l.external_listing_id,
            "message": (
                f'"{plan.loser.variant_name}" is live on {l.platform.capitalize()}'
                + (f" as {l.published_sku}" if l.published_sku else "")
                + ". Merging sets that variation's quantity to 0; remove it on the marketplace afterwards."
            ),
        }
        for l in plan.live_listings
    ]
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "live_listing_conflicts",
            "message": f"This variant is live on {len(conflicts)} marketplace listing(s). Nothing has been changed.",
            "conflicts": conflicts,
        },
    )


async def _copy_overrides(session: AsyncSession, model, loser_id: int, survivor_id: int) -> None:
    """Survivor takes the loser's override rows. Delete, flush, then insert — the unique
    index on (variant, material, replaced line) would otherwise see both sets at once."""
    for row in (await session.execute(select(model).where(model.variant_id == survivor_id))).scalars():
        await session.delete(row)
    await session.flush()
    for row in (await session.execute(select(model).where(model.variant_id == loser_id))).scalars():
        session.add(
            model(
                variant_id=survivor_id,
                material_id=row.material_id,
                qty_required=row.qty_required,
                replaces_material_id=row.replaces_material_id,
            )
        )
        await session.delete(row)
    await session.flush()


async def _drop_overrides(session: AsyncSession, model, variant_id: int) -> None:
    for row in (await session.execute(select(model).where(model.variant_id == variant_id))).scalars():
        await session.delete(row)
    await session.flush()


async def apply_merge(
    session: AsyncSession,
    loser_id: int,
    survivor_id: int,
    *,
    bom: MergeBomChoice = "keep_survivor",
    kitting: MergeBomChoice = "keep_survivor",
    on_live_listing: LiveListingResolution = "ask",
    commit: bool = True,
) -> MergeOutcome:
    """Does it. Raises VariantMergeError (400) or the live-listing HTTPException (409)
    before anything is written; after that, marketplace push failures are collected as
    warnings rather than raised, because the local merge is already the truth.

    `commit=False` lets services/attribute_values fold several pairs into one transaction.
    """
    plan = await plan_merge(session, loser_id, survivor_id)
    if plan.blockers:
        raise VariantMergeError(" ".join(plan.blockers))
    require_no_live_listing_conflicts(plan, on_live_listing)

    loser = await session.get(ProductVariant, loser_id)
    survivor = await session.get(ProductVariant, survivor_id)
    product = await session.get(Product, loser.product_id)
    product_id = loser.product_id
    reason = f'Merged "{loser.variant_name}" into "{survivor.variant_name}"'
    warnings: list[str] = []

    # 1. Marketplace first, while the loser is still active and so still pushable. Push
    #    failures do not stop the merge — the listing will show in the sync report.
    for listing in await _live_listings(session, loser.id):
        push_status, message = await listing_push._push_one(session, listing, 0)
        if push_status is not None and push_status != ListingPushStatus.success:
            warnings.append(
                f"{listing.platform.value} listing for \"{loser.variant_name}\" could not be set to 0"
                + (f": {message}" if message else "")
                + " — check it on the marketplace."
            )

    # 2. Open order lines follow the units they were going to be filled from.
    open_lines = await _open_lines(session, loser.id)
    for line in open_lines:
        await order_substitution.substitute_line(
            session, line, survivor.id, line.ordered_qty - line.shipped_qty, reason
        )
    await session.flush()
    await session.refresh(loser)
    await session.refresh(survivor)

    # 3. Stock. Two adjustments so each ledger tells the story on its own.
    stock_moved = loser.current_stock
    if stock_moved > 0:
        await stock_adjustments.create_stock_adjustment(
            session, product_id, loser.id, StockAdjustmentMode.adjust, -stock_moved, reason, commit=False
        )
        await stock_adjustments.create_stock_adjustment(
            session, product_id, survivor.id, StockAdjustmentMode.adjust, stock_moved, reason, commit=False
        )
        # The lines moved in step 2 were allocated against whatever the survivor had *then*;
        # the units that were reserved for them on the loser have only just arrived.
        for order_id in {line.order_id for line in open_lines}:
            await allocation.allocate_order(session, await session.get(Order, order_id), source="variant-merge")
        await session.refresh(survivor)

    # 4. Recipes: one survives, by the user's choice. Identical ones need no choice.
    if bom == "take_loser" and plan.bom_differs:
        await _copy_overrides(session, ProductVariantMaterial, loser.id, survivor.id)
    else:
        await _drop_overrides(session, ProductVariantMaterial, loser.id)
    if kitting == "take_loser" and plan.kitting_differs:
        await _copy_overrides(session, ProductVariantKittingMaterial, loser.id, survivor.id)
    else:
        await _drop_overrides(session, ProductVariantKittingMaterial, loser.id)

    # 5. Things that were merely attached to the loser attach to the survivor instead.
    for asset in (await session.execute(select(ProductAsset).where(ProductAsset.variant_id == loser.id))).scalars():
        asset.variant_id = survivor.id
    for alias in (await session.execute(select(SkuAlias).where(SkuAlias.variant_id == loser.id))).scalars():
        alias.variant_id = survivor.id
    # And the loser's own SKU becomes an alias, so an order still quoting it — a
    # marketplace listing not yet cleaned up, a customer's reorder — resolves to the
    # survivor (services/variants.find_by_sku consults aliases first).
    loser_sku = compute_full_sku(product.sku, loser.sku_suffix)
    if loser_sku and loser_sku != compute_full_sku(product.sku, survivor.sku_suffix):
        existing = {
            row.platform
            for row in (
                await session.execute(select(SkuAlias).where(SkuAlias.external_sku == loser_sku))
            ).scalars()
        }
        for platform in ListingPlatform:
            if platform not in existing:
                session.add(
                    SkuAlias(platform=platform, external_sku=loser_sku, product_id=product_id, variant_id=survivor.id)
                )

    # 6. Retire. The Listing rows stay: current_unit_filter excludes them from every push
    #    from here on, and the sync check keeps reporting what the marketplace still has.
    loser.is_active = False

    if commit:
        await session.commit()
        await session.refresh(survivor)
    listing_push.enqueue_for_owner(survivor)
    return MergeOutcome(
        survivor=survivor, stock_moved=stock_moved, open_lines_moved=len(open_lines), warnings=warnings
    )
