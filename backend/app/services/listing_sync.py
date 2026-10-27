from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import PlatformListingPush
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.listing import (
    AdditionalListingResult,
    BulkListingSyncResult,
    ListingSyncStatus,
    ProductListingSyncSummary,
    ProductSyncStatus,
    UnitSyncResult,
)
from app.services import listing_push, product_platforms
from app.services.platforms.base import ExternalListingRef
from app.services.variants import compute_full_sku


async def _get_or_create_listing(
    session: AsyncSession, product_id: int, variant_id: int | None, platform: ListingPlatform
) -> Listing:
    variant_filter = Listing.variant_id.is_(None) if variant_id is None else Listing.variant_id == variant_id
    result = await session.execute(
        select(Listing).where(
            Listing.product_id == product_id,
            variant_filter,
            Listing.platform == platform,
            Listing.is_primary.is_(True),
        )
    )
    listing = result.scalar_one_or_none()
    if listing is None:
        listing = Listing(product_id=product_id, variant_id=variant_id, platform=platform, is_primary=True)
        session.add(listing)
    return listing


def split_multi_index(
    multi: dict[str, list[ExternalListingRef]],
) -> tuple[dict[str, ExternalListingRef], dict[str, list[ExternalListingRef]]]:
    """Splits an every-listing-per-SKU index into the one-per-SKU index the sync check has
    always taken plus the remaining listings per SKU.

    The first ref (an active listing in preference to an inactive one, otherwise crawl
    order) is the default primary; check_product_sku_sync keeps an existing primary stable
    when it is still among the candidates. One listing appearing under a SKU more than once
    (a listing whose variations all share a SKU) is one listing, not several."""
    index: dict[str, ExternalListingRef] = {}
    extras: dict[str, list[ExternalListingRef]] = {}
    for sku, refs in multi.items():
        distinct: list[ExternalListingRef] = []
        seen: set[str] = set()
        for ref in sorted(refs, key=lambda r: r.state != "active"):
            if ref.external_listing_id not in seen:
                seen.add(ref.external_listing_id)
                distinct.append(ref)
        index[sku] = distinct[0]
        if len(distinct) > 1:
            extras[sku] = distinct[1:]
    return index, extras


async def fetch_sku_index(
    adapter, session: AsyncSession, connection, **kwargs
) -> tuple[dict[str, ExternalListingRef], dict[str, list[ExternalListingRef]]]:
    """The marketplace's SKU index, as (index, extras) — see split_multi_index. Adapters
    that can see several listings under one SKU (Etsy) expose build_listing_sku_multi_index;
    for the rest the extras are empty and behaviour is exactly the single-listing one."""
    builder = getattr(adapter, "build_listing_sku_multi_index", None)
    if builder is None:
        return await adapter.build_listing_sku_index(session, connection, **kwargs), {}
    return split_multi_index(await builder(session, connection, **kwargs))


def _apply_match(listing: Listing, match: ExternalListingRef, sku: str | None, now: datetime) -> None:
    listing.external_listing_id = match.external_listing_id
    listing.external_title = match.title
    listing.external_variation = match.variation
    listing.external_state = match.state
    listing.external_quantity = match.quantity
    listing.last_checked_at = now
    # A match is a marketplace confirming it holds this SKU, which is the only moment
    # that fact is knowable. Deliberately not cleared on a miss: a listing that has gone
    # inactive is still published under this SKU, and forgetting that would let a later
    # rename change an identifier that is still in use out there.
    if sku:
        listing.published_sku = sku


async def _unit_rows(
    session: AsyncSession, product_id: int, variant_id: int | None, platform: ListingPlatform
) -> list[Listing]:
    variant_filter = Listing.variant_id.is_(None) if variant_id is None else Listing.variant_id == variant_id
    result = await session.execute(
        select(Listing)
        .where(Listing.product_id == product_id, variant_filter, Listing.platform == platform)
        .order_by(Listing.id)
    )
    return list(result.scalars())


async def _delete_additional(session: AsyncSession, listing: Listing) -> None:
    """Removes an additional listing row along with its push-attempt log. Left behind, the
    log's listing_id would be nulled by the FK, and sync_status reads a NULL listing_id as
    "an attempt against the unit, from before listings were tracked individually" — so a
    failure on a listing that no longer exists would count against the unit forever."""
    await session.execute(delete(PlatformListingPush).where(PlatformListingPush.listing_id == listing.id))
    await session.delete(listing)


async def _id_of(session: AsyncSession, listing: Listing) -> int:
    if listing.id is None:
        await session.flush()
    return listing.id


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
    extra_index: dict[str, list[ExternalListingRef]] | None = None,
) -> ProductListingSyncSummary:
    """Tests the product's own SKU (if it has no variants) or every active variant's
    full SKU independently against an already-built listing index, persisting the
    result of each check onto its Listing row.

    with_expected_quantity additionally computes what listing_push would send for each
    unit, so the UI can flag a listing whose quantity has drifted. Off by default because
    it is a per-unit buildability computation: the shop-wide check (which reuses this
    per product) would turn one click into one such computation per unit in the catalogue,
    and it only needs statuses.

    extra_index holds the further listings per SKU beyond the one in `index` (see
    split_multi_index). Each gets its own non-primary Listing row so quantity pushes reach
    it; rows for listings that no longer hold the SKU are deleted, since they are derived
    state the next check rebuilds. The primary stays whichever listing it already is while
    that listing still holds the SKU, so a seller's A/B ordering doesn't flip between
    checks."""
    product = await session.get(Product, product_id)
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    active_variants = await _active_variants(session, product_id)
    now = datetime.now(timezone.utc)

    units: list[UnitSyncResult] = []
    checks = _unit_checks(product, active_variants)

    for variant_id, variant_name, sku in checks:
        match = index.get(sku) if sku else None
        extras = list(extra_index.get(sku, [])) if sku and extra_index else []
        rows = await _unit_rows(session, product_id, variant_id, platform)
        current_primary = next((r for r in rows if r.is_primary), None)
        if match is not None and current_primary is not None and current_primary.external_listing_id:
            for i, ref in enumerate(extras):
                if ref.external_listing_id == current_primary.external_listing_id:
                    match, extras[i] = ref, match
                    break

        # Additional rows that are stale, or about to become the primary, go first and are
        # flushed before anything is written: the unique index on (unit, listing) would
        # otherwise see the promotion's UPDATE before the row's DELETE.
        keep_ids = {ref.external_listing_id for ref in extras}
        deleted_rows = set()
        for row in rows:
            if not row.is_primary and (
                row.external_listing_id not in keep_ids
                or (match is not None and row.external_listing_id == match.external_listing_id)
            ):
                await _delete_additional(session, row)
                deleted_rows.add(row.id)
        await session.flush()

        listing = await _get_or_create_listing(session, product_id, variant_id, platform)
        if match is not None:
            _apply_match(listing, match, sku, now)
        else:
            listing.external_listing_id = None
            listing.external_title = None
            listing.external_variation = None
            listing.external_state = None
            listing.external_quantity = None
            listing.last_checked_at = now
        await session.flush()

        status = _status_from_match(match)
        expected_quantity = (
            await listing_push.resolve_push_quantity(session, product_id, variant_id)
            if with_expected_quantity
            else None
        )

        extra_rows = {r.external_listing_id: r for r in rows if not r.is_primary and r.id not in deleted_rows}
        additional: list[AdditionalListingResult] = []
        for ref in extras:
            extra_row = extra_rows.get(ref.external_listing_id)
            if extra_row is None:
                extra_row = Listing(product_id=product_id, variant_id=variant_id, platform=platform, is_primary=False)
                session.add(extra_row)
            _apply_match(extra_row, ref, sku, now)
            extra_status = _status_from_match(ref)
            additional.append(
                AdditionalListingResult(
                    listing_id=await _id_of(session, extra_row),
                    external_listing_id=ref.external_listing_id,
                    role=extra_row.role,
                    status=extra_status,
                    external_title=ref.title,
                    external_variation=ref.variation,
                    external_state=ref.state,
                    external_quantity=ref.quantity,
                    last_checked_at=now,
                    quantity_mismatch=_quantity_mismatch(extra_status, ref.quantity, expected_quantity),
                )
            )

        units.append(
            UnitSyncResult(
                listing_id=await _id_of(session, listing),
                role=listing.role,
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
                quantity_mismatch=_quantity_mismatch(status, listing.external_quantity, expected_quantity)
                or any(a.quantity_mismatch for a in additional),
                additional_listings=additional,
            )
        )

    await _unlink_stale_unit_rows(session, product_id, platform, {variant_id for variant_id, _, _ in checks}, now)

    await session.commit()
    status = rollup_product_status([u.status for u in units])
    return ProductListingSyncSummary(product_id=product_id, product_status=status, units=units)


_MAX_ROLE_LENGTH = 60


async def update_listing(
    session: AsyncSession, listing_id: int, role_given: bool, role: str | None, make_primary: bool
) -> Listing:
    """Edits a seller-controlled field of one listings row: its label, and which of a unit's
    listings is the primary. Raises ValueError for an unknown row or an over-long label.

    Promotion swaps the flag with the unit's current primary in two flushes — the partial
    unique index allows one primary per unit, so the demotion has to land first."""
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise ValueError(f"Listing {listing_id} not found")

    if role_given:
        cleaned = (role or "").strip() or None
        if cleaned is not None and len(cleaned) > _MAX_ROLE_LENGTH:
            raise ValueError(f"A listing label can be at most {_MAX_ROLE_LENGTH} characters")
        listing.role = cleaned

    if make_primary and not listing.is_primary:
        current = next(
            (
                r
                for r in await _unit_rows(session, listing.product_id, listing.variant_id, listing.platform)
                if r.is_primary
            ),
            None,
        )
        if current is not None:
            current.is_primary = False
            await session.flush()
        listing.is_primary = True

    await session.commit()
    return listing


async def _unlink_stale_unit_rows(
    session: AsyncSession, product_id: int, platform: ListingPlatform, checked_unit_ids: set[int | None], now: datetime
) -> None:
    """Forget the marketplace link on any row for a unit this check no longer covers.

    The rows exist because a product's shape can change after it was linked: a product
    checked as itself and then given variants keeps its product-level row; a variant that
    is deactivated keeps its row. Neither is ever rendered or re-checked (both come off
    _unit_checks, which excludes them), so nothing would otherwise ever clear an
    external_listing_id that is now wrong — and services/listing_units keeps the push
    paths off such rows precisely because it *is* wrong: the parent SKU is not what the
    listing carries once its variants are. Clearing the link here makes the stored state
    say what the push paths already assume, so live_platforms and the reconcile sweep
    stop seeing a listing that isn't there.

    published_sku is deliberately left alone, same as a miss on a current unit — the
    marketplace did acknowledge that SKU once, and sku_generation still needs to know."""
    result = await session.execute(
        select(Listing).where(
            Listing.product_id == product_id,
            Listing.platform == platform,
            Listing.external_listing_id.is_not(None),
        )
    )
    for listing in result.scalars():
        if listing.variant_id in checked_unit_ids:
            continue
        if not listing.is_primary:
            # An additional listing for a unit this check no longer covers: derived state
            # with nothing worth keeping.
            await _delete_additional(session, listing)
            continue
        listing.external_listing_id = None
        listing.external_title = None
        listing.external_variation = None
        listing.external_state = None
        listing.external_quantity = None
        listing.last_checked_at = now


async def check_all_products_sku_sync(
    session: AsyncSession,
    index: dict[str, ExternalListingRef],
    platform: ListingPlatform,
    extra_index: dict[str, list[ExternalListingRef]] | None = None,
) -> BulkListingSyncResult:
    """Builds on one shared listing index (the expensive part) across every active
    product, so a shop-wide check costs the same one marketplace fetch as a
    single-product check."""
    result = await session.execute(select(Product.id).where(Product.is_active.is_(True)))
    # Products switched off for this store are left alone: checking them writes Listing
    # rows for a marketplace they were never meant for.
    excluded = await product_platforms.excluded_product_ids(session, platform)
    product_ids = [row[0] for row in result if row[0] not in excluded]

    summaries = [await check_product_sku_sync(session, pid, index, platform, extra_index=extra_index) for pid in product_ids]
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
        select(Listing).where(Listing.product_id == product_id, Listing.platform == platform).order_by(Listing.id)
    )
    listing_by_variant: dict[int | None, Listing] = {}
    additional_by_variant: dict[int | None, list[Listing]] = {}
    for row in result.scalars():
        if row.is_primary:
            listing_by_variant[row.variant_id] = row
        else:
            additional_by_variant.setdefault(row.variant_id, []).append(row)

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
        additional = [
            AdditionalListingResult(
                listing_id=extra.id,
                external_listing_id=extra.external_listing_id,
                role=extra.role,
                status=_status_from_stored(extra),
                external_title=extra.external_title,
                external_variation=extra.external_variation,
                external_state=extra.external_state,
                external_quantity=extra.external_quantity,
                last_checked_at=extra.last_checked_at,
                quantity_mismatch=_quantity_mismatch(
                    _status_from_stored(extra), extra.external_quantity, expected_quantity
                ),
            )
            for extra in additional_by_variant.get(variant_id, [])
        ]
        units.append(
            UnitSyncResult(
                listing_id=listing.id if listing else None,
                role=listing.role if listing else None,
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
                quantity_mismatch=_quantity_mismatch(status, external_quantity, expected_quantity)
                or any(a.quantity_mismatch for a in additional),
                additional_listings=additional,
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

    listings_result = await session.execute(
        select(Listing).where(Listing.platform == platform, Listing.is_primary.is_(True))
    )
    listing_by_unit = {(listing.product_id, listing.variant_id): listing for listing in listings_result.scalars()}

    result: dict[int, ProductSyncStatus] = {}
    for product_id in product_ids:
        variant_ids = variant_ids_by_product.get(product_id, [])
        unit_keys = [(product_id, None)] if not variant_ids else [(product_id, vid) for vid in variant_ids]
        statuses = [_status_from_stored(listing_by_unit.get(key)) for key in unit_keys]
        result[product_id] = rollup_product_status(statuses)
    return result
