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
from app.services import listing_push
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


def _unit_checks(
    product: Product, active_variants: list[ProductVariant]
) -> list[tuple[int | None, str | None, str | None]]:
    """The units a sync check covers for one product, as (variant_id, variant_name, sku):
    the product itself when it has no active variants, otherwise one per active variant.

    The single definition of "what gets checked" — tracked_skus derives from it too, so
    the SKUs an adapter is asked to enrich can't drift from the SKUs actually looked up.
    Drift either way is silent: enrich a SKU that's never checked and it's wasted API
    budget; check one that was never enriched and it shows permanently coarse detail."""
    if not active_variants:
        return [(None, None, product.sku)]
    return [(v.id, v.variant_name, compute_full_sku(product.sku, v.sku_suffix)) for v in active_variants]


def _unit_skus(product: Product, active_variants: list[ProductVariant]) -> list[str]:
    return [sku for _, _, sku in _unit_checks(product, active_variants) if sku]


async def product_skus(session: AsyncSession, product_id: int) -> set[str]:
    """The SKUs a sync check would look up for one product — the single-product analogue
    of tracked_skus, for scoping adapter enrichment on a single-product check.

    An unknown product yields an empty set rather than raising: the caller checks it
    immediately afterwards and reports a 404 with better wording than this could."""
    product = await session.get(Product, product_id)
    if product is None:
        return set()
    return set(_unit_skus(product, await _active_variants(session, product_id)))


async def tracked_skus(session: AsyncSession) -> set[str]:
    """Every SKU any sync check would look up, across all active products.

    Lets an adapter scope expensive per-SKU enrichment to what StockSmith actually cares
    about — a seller with 2,000 marketplace SKUs and 80 here shouldn't pay for 2,000
    lookups. Two queries, no N+1."""
    products = (
        await session.execute(select(Product).where(Product.is_active.is_(True)))
    ).scalars().all()
    variants = (
        await session.execute(
            select(ProductVariant)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(Product.is_active.is_(True), ProductVariant.is_active.is_(True))
        )
    ).scalars().all()

    variants_by_product: dict[int, list[ProductVariant]] = {}
    for variant in variants:
        variants_by_product.setdefault(variant.product_id, []).append(variant)

    return {
        sku
        for product in products
        for sku in _unit_skus(product, variants_by_product.get(product.id, []))
    }


def _quantity_mismatch(status: ListingSyncStatus, external_quantity: int | None, expected: int | None) -> bool:
    """A mismatch is only meaningful for a listing that was actually found — a SKU the
    marketplace doesn't have is a "not found" problem, and offering to "correct" its
    quantity would be offering to push to nothing."""
    if status != ListingSyncStatus.synced or external_quantity is None or expected is None:
        return False
    return external_quantity != expected


async def check_product_sku_sync(
    session: AsyncSession,
    product_id: int,
    index: dict[str, ExternalListingRef],
    platform: ListingPlatform,
    *,
    with_expected_quantity: bool = False,
) -> ProductListingSyncSummary:
    """Tests the product's own SKU (if it has no variants) or every active variant's
    full SKU independently against an already-built listing index, persisting the
    result of each check onto its Listing row.

    with_expected_quantity additionally computes what listing_push would send for each
    unit, so the UI can flag a listing whose quantity has drifted. Off by default because
    it is a per-unit buildability computation: the shop-wide check (which reuses this
    per product) would turn one click into one such computation per unit in the catalogue,
    and it only needs statuses."""
    product = await session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    active_variants = await _active_variants(session, product_id)
    now = datetime.now(timezone.utc)

    units: list[UnitSyncResult] = []
    checks = _unit_checks(product, active_variants)

    for variant_id, variant_name, sku in checks:
        match = index.get(sku) if sku else None
        listing = await _get_or_create_listing(session, product_id, variant_id, platform)
        listing.external_listing_id = match.external_listing_id if match else None
        listing.external_title = match.title if match else None
        listing.external_variation = match.variation if match else None
        listing.external_state = match.state if match else None
        listing.external_quantity = match.quantity if match else None
        listing.last_checked_at = now
        # A match is a marketplace confirming it holds this SKU, which is the only moment
        # that fact is knowable. Deliberately not cleared on a miss: a listing that has gone
        # inactive is still published under this SKU, and forgetting that would let a later
        # rename change an identifier that is still in use out there.
        if match is not None and sku:
            listing.published_sku = sku

        status = _status_from_match(match)
        expected_quantity = (
            await listing_push.resolve_push_quantity(session, product_id, variant_id)
            if with_expected_quantity
            else None
        )
        units.append(
            UnitSyncResult(
                variant_id=variant_id,
                variant_name=variant_name,
                sku=sku,
                status=status,
                external_listing_id=listing.external_listing_id,
                external_title=listing.external_title,
                external_variation=listing.external_variation,
                external_state=listing.external_state,
                external_quantity=listing.external_quantity,
                last_checked_at=listing.last_checked_at,
                expected_quantity=expected_quantity,
                quantity_mismatch=_quantity_mismatch(status, listing.external_quantity, expected_quantity),
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

    checks = _unit_checks(product, active_variants)

    units = []
    for variant_id, variant_name, sku in checks:
        listing = listing_by_variant.get(variant_id)
        status = _status_from_stored(listing)
        external_quantity = listing.external_quantity if listing else None
        # Always computed here, unlike check_product_sku_sync: this function is only ever
        # called for a single product (the Platform Sync tab's page load), never fanned
        # out across the catalogue, so there's no bulk path to keep cheap.
        expected_quantity = await listing_push.resolve_push_quantity(session, product_id, variant_id)
        units.append(
            UnitSyncResult(
                variant_id=variant_id,
                variant_name=variant_name,
                sku=sku,
                status=status,
                external_listing_id=listing.external_listing_id if listing else None,
                external_title=listing.external_title if listing else None,
                external_variation=listing.external_variation if listing else None,
                external_state=listing.external_state if listing else None,
                external_quantity=external_quantity,
                last_checked_at=listing.last_checked_at if listing else None,
                expected_quantity=expected_quantity,
                quantity_mismatch=_quantity_mismatch(status, external_quantity, expected_quantity),
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
