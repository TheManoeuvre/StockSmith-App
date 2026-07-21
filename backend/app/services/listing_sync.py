from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.listing import (
    BulkListingSyncResult,
    ListingSyncStatus,
    ProductListingSyncSummary,
    ProductSyncStatus,
    UnitSyncResult,
)
from app.services.platforms.base import ExternalListingRef
from app.services.variants import compute_full_sku


async def _get_or_create_listing(
    session: AsyncSession, product_id: int, variant_id: int | None, platform: ListingPlatform
) -> Listing:
    variant_filter = Listing.variant_id.is_(None) if variant_id is None else Listing.variant_id == variant_id
    result = await session.execute(
        select(Listing).where(Listing.product_id == product_id, variant_filter, Listing.platform == platform)
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        listing = Listing(product_id=product_id, variant_id=variant_id, platform=platform)
        session.add(listing)
    return listing


def _status_from_match(match: ExternalListingRef | None) -> ListingSyncStatus:
    if match is None:
        return ListingSyncStatus.not_found
    return ListingSyncStatus.synced if match.state == "active" else ListingSyncStatus.listing_not_active


def _status_from_stored(listing: Listing | None) -> ListingSyncStatus:
    if listing is None or listing.last_checked_at is None:
        return ListingSyncStatus.not_tested
    if listing.external_listing_id is None:
        return ListingSyncStatus.not_found
    return ListingSyncStatus.synced if listing.external_state == "active" else ListingSyncStatus.listing_not_active


def rollup_product_status(statuses: list[ListingSyncStatus]) -> ProductSyncStatus:
    """Synced only if every active unit passed; not_tested only if none have ever been
    checked; not_found only once every unit has been checked and none passed. Any other
    mix (some passed / some still untested) shows as partial — a product can't claim
    full sync until every active variant has been confirmed."""
    if not statuses or all(s == ListingSyncStatus.not_tested for s in statuses):
        return ProductSyncStatus.not_tested
    synced_flags = [s == ListingSyncStatus.synced for s in statuses]
    if all(synced_flags):
        return ProductSyncStatus.synced
    if any(synced_flags) or any(s == ListingSyncStatus.not_tested for s in statuses):
        return ProductSyncStatus.partial
    return ProductSyncStatus.not_found


async def _active_variants(session: AsyncSession, product_id: int) -> list[ProductVariant]:
    result = await session.execute(
        select(ProductVariant).where(ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True))
    )
    return list(result.scalars())


async def check_product_sku_sync(
    session: AsyncSession,
    product_id: int,
    index: dict[str, ExternalListingRef],
    platform: ListingPlatform,
) -> ProductListingSyncSummary:
    """Tests the product's own SKU (if it has no variants) or every active variant's
    full SKU independently against an already-built listing index, persisting the
    result of each check onto its Listing row."""
    product = await session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    active_variants = await _active_variants(session, product_id)
    now = datetime.now(timezone.utc)

    units: list[UnitSyncResult] = []
    checks: list[tuple[int | None, str | None, str | None]] = (
        [(None, None, product.sku)]
        if not active_variants
        else [(v.id, v.variant_name, compute_full_sku(product.sku, v.sku_suffix)) for v in active_variants]
    )

    for variant_id, variant_name, sku in checks:
        match = index.get(sku) if sku else None
        listing = await _get_or_create_listing(session, product_id, variant_id, platform)
        listing.external_listing_id = match.external_listing_id if match else None
        listing.external_title = match.title if match else None
        listing.external_variation = match.variation if match else None
        listing.external_state = match.state if match else None
        listing.external_quantity = match.quantity if match else None
        listing.last_checked_at = now

        units.append(
            UnitSyncResult(
                variant_id=variant_id,
                variant_name=variant_name,
                sku=sku,
                status=_status_from_match(match),
                external_listing_id=listing.external_listing_id,
                external_title=listing.external_title,
                external_variation=listing.external_variation,
                external_state=listing.external_state,
                external_quantity=listing.external_quantity,
                last_checked_at=listing.last_checked_at,
            )
        )

    await session.commit()
    status = rollup_product_status([u.status for u in units])
    return ProductListingSyncSummary(product_id=product_id, product_status=status, units=units)


async def check_all_products_sku_sync(
    session: AsyncSession, index: dict[str, ExternalListingRef], platform: ListingPlatform
) -> BulkListingSyncResult:
    """Builds on one shared listing index (the expensive part) across every active
    product, so a shop-wide check costs the same one marketplace fetch as a
    single-product check."""
    result = await session.execute(select(Product.id).where(Product.is_active.is_(True)))
    product_ids = [row[0] for row in result]

    summaries = [await check_product_sku_sync(session, pid, index, platform) for pid in product_ids]
    return BulkListingSyncResult(
        summaries=summaries,
        synced_count=sum(1 for s in summaries if s.product_status == ProductSyncStatus.synced),
        partial_count=sum(1 for s in summaries if s.product_status == ProductSyncStatus.partial),
        not_found_count=sum(1 for s in summaries if s.product_status == ProductSyncStatus.not_found),
    )


async def get_stored_product_sync_status(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> ProductListingSyncSummary:
    """Reads back the last check's results without hitting the marketplace again — for
    page load."""
    product = await session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    active_variants = await _active_variants(session, product_id)
    result = await session.execute(
        select(Listing).where(Listing.product_id == product_id, Listing.platform == platform)
    )
    listing_by_variant = {listing.variant_id: listing for listing in result.scalars()}

    checks: list[tuple[int | None, str | None, str | None]] = (
        [(None, None, product.sku)]
        if not active_variants
        else [(v.id, v.variant_name, compute_full_sku(product.sku, v.sku_suffix)) for v in active_variants]
    )

    units = []
    for variant_id, variant_name, sku in checks:
        listing = listing_by_variant.get(variant_id)
        units.append(
            UnitSyncResult(
                variant_id=variant_id,
                variant_name=variant_name,
                sku=sku,
                status=_status_from_stored(listing),
                external_listing_id=listing.external_listing_id if listing else None,
                external_title=listing.external_title if listing else None,
                external_variation=listing.external_variation if listing else None,
                external_state=listing.external_state if listing else None,
                external_quantity=listing.external_quantity if listing else None,
                last_checked_at=listing.last_checked_at if listing else None,
            )
        )

    status = rollup_product_status([u.status for u in units])
    return ProductListingSyncSummary(product_id=product_id, product_status=status, units=units)


async def get_all_stored_sync_status(
    session: AsyncSession, platform: ListingPlatform
) -> dict[int, ProductSyncStatus]:
    """Cheap, marketplace-free rollup per active product from already-stored Listing
    rows — for list views that only need a badge, not full per-unit detail."""
    products_result = await session.execute(select(Product.id).where(Product.is_active.is_(True)))
    product_ids = [row[0] for row in products_result]

    variants_result = await session.execute(
        select(ProductVariant.id, ProductVariant.product_id).where(ProductVariant.is_active.is_(True))
    )
    variant_ids_by_product: dict[int, list[int]] = {}
    for variant_id, product_id in variants_result:
        variant_ids_by_product.setdefault(product_id, []).append(variant_id)

    listings_result = await session.execute(select(Listing).where(Listing.platform == platform))
    listing_by_unit = {(listing.product_id, listing.variant_id): listing for listing in listings_result.scalars()}

    result: dict[int, ProductSyncStatus] = {}
    for product_id in product_ids:
        variant_ids = variant_ids_by_product.get(product_id, [])
        unit_keys = [(product_id, None)] if not variant_ids else [(product_id, vid) for vid in variant_ids]
        statuses = [_status_from_stored(listing_by_unit.get(key)) for key in unit_keys]
        result[product_id] = rollup_product_status(statuses)
    return result
