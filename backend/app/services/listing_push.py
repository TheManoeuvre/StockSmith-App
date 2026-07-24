import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import buildability, kitting
from app.services.platforms import get_adapter
from app.services.platforms.base import ExternalListingRef
from app.services.variants import compute_full_sku

logger = logging.getLogger("stocksmith.listing_push")

"""Pushes a product/variant's current max_sellable to every connected marketplace
listing for it, whenever local stock actually changes — the outbound half of the
allocated/consumed-means-unavailable rule; kitting.compute_max_sellable (the existing
"what's actually sellable right now" computation) already implements the rule itself, so
this module is purely the "tell the marketplace" leg. See
docs/plan-marketplace-integrations.md Section 1d."""

# How long to wait after the last enqueue for a given (product_id, variant_id) before
# actually pushing — coalesces a burst of changes (importing a batch of orders, a build
# immediately followed by allocation) into one outbound call per listing, and — because
# the quantity is recomputed fresh at send time, not captured at enqueue time — a later
# enqueue during the wait naturally picks up the newest state without resetting anything.
_DEBOUNCE_SECONDS = 5

# Platforms whose adapter implements push_listing_quantity — both do now (Section 3/
# cross-platform push wiring). This is the entire generalization: _push_now already
# queries Listing rows by platform generically, so enabling a platform here is the only
# change needed for it to start receiving pushes.
_PUSH_ENABLED_PLATFORMS = (ListingPlatform.etsy, ListingPlatform.ebay)

_pending: dict[tuple[int, int | None], asyncio.Task] = {}

_PRODUCTS_USING_MATERIAL_SQL = text(
    """
    SELECT product_id, NULL AS variant_id FROM product_materials WHERE material_id = :material_id
    UNION
    SELECT product_id, NULL AS variant_id FROM product_kitting_materials WHERE material_id = :material_id
    -- Every active variant of a product matched above inherits the base BOM unless it
    -- overrides this specific material away — included unconditionally rather than
    -- resolving each variant's effective BOM here (a debounced, idempotent recompute is
    -- cheap; an occasional redundant push for a variant that substituted this material
    -- away is a fine trade for not silently under-notifying).
    UNION
    SELECT v.product_id, v.id AS variant_id
    FROM product_variants v
    WHERE v.is_active = true AND v.product_id IN (
        SELECT product_id FROM product_materials WHERE material_id = :material_id
        UNION
        SELECT product_id FROM product_kitting_materials WHERE material_id = :material_id
    )
    UNION
    SELECT v.product_id, pvm.variant_id
    FROM product_variant_materials pvm
    JOIN product_variants v ON v.id = pvm.variant_id
    WHERE v.is_active = true AND (pvm.material_id = :material_id OR pvm.replaces_material_id = :material_id)
    UNION
    SELECT v.product_id, pvkm.variant_id
    FROM product_variant_kitting_materials pvkm
    JOIN product_variants v ON v.id = pvkm.variant_id
    WHERE v.is_active = true AND (pvkm.material_id = :material_id OR pvkm.replaces_material_id = :material_id)
    """
)


def enqueue_for_product(product_id: int, variant_id: int | None = None) -> None:
    """Same as enqueue_for_owner, for callers that only have IDs on hand (e.g. looping
    over a product's variant IDs after a platform_ceiling_qty edit) rather than a loaded
    Product/ProductVariant ORM object."""
    _enqueue(product_id, variant_id)


def enqueue_for_owner(owner: Product | ProductVariant) -> None:
    """Fire-and-forget: schedules a debounced push for whichever product/variant `owner`
    is. Safe to call before the caller's own transaction commits — the push itself runs
    _DEBOUNCE_SECONDS later against a fresh session, well after any normal request's
    commit has landed."""
    if isinstance(owner, ProductVariant):
        _enqueue(owner.product_id, owner.id)
    else:
        _enqueue(owner.id, None)


async def enqueue_for_material(session: AsyncSession, material_id: int) -> None:
    """Fans a material-quantity change out to every product/variant whose build or
    kitting BOM references it — the material-consumption half of the allocated/consumed-
    means-unavailable rule (the owner-based enqueue_for_owner above only covers direct
    Product/ProductVariant stock changes)."""
    result = await session.execute(_PRODUCTS_USING_MATERIAL_SQL, {"material_id": material_id})
    for row in result:
        _enqueue(row.product_id, row.variant_id)


def _enqueue(product_id: int, variant_id: int | None) -> None:
    key = (product_id, variant_id)
    if key in _pending:
        return  # already scheduled — the eventual push recomputes fresh, nothing to reset
    _pending[key] = asyncio.create_task(_debounced_push(key))


async def _debounced_push(key: tuple[int, int | None]) -> None:
    try:
        await asyncio.sleep(_DEBOUNCE_SECONDS)
        product_id, variant_id = key
        async with async_session_factory() as session:
            await _push_now(session, product_id, variant_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unexpected error pushing listing quantity for product_id=%s variant_id=%s", *key)
    finally:
        _pending.pop(key, None)


async def _resolve_max_sellable(session: AsyncSession, product_id: int, variant_id: int | None) -> int | None:
    """Recomputes max_sellable fresh — the same computation products.py/variants.py use
    to serialize a single product/variant, run here for just the one item rather than
    the bulk list views those routers build for. Deliberately never expected_max_sellable
    (which counts on-order/not-yet-received stock) — pushing "expected" would let a
    marketplace sell something not physically in hand yet."""
    product = await session.get(Product, product_id)
    if product is None:
        return None

    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        if variant is None:
            return None
        _, expected_max_buildable, _, _ = await buildability.compute_variant_buildability(
            session, product_id, variant_id
        )
        current_stock, allocated_qty = variant.current_stock, variant.allocated_qty
    else:
        expected_max_buildable_by_product = await buildability.get_expected_max_buildable_by_product(session)
        expected_max_buildable = expected_max_buildable_by_product.get(product_id)
        current_stock, allocated_qty = product.current_stock, product.allocated_qty

    max_sellable, _, _, _, _ = await kitting.compute_max_sellable(
        session,
        product_id,
        variant_id,
        current_stock,
        allocated_qty,
        expected_max_buildable,
        product.platform_ceiling_qty,
    )
    return max_sellable


async def _resolve_sku(session: AsyncSession, listing: Listing) -> str | None:
    product = await session.get(Product, listing.product_id)
    if product is None:
        return None
    if listing.variant_id is None:
        return product.sku
    variant = await session.get(ProductVariant, listing.variant_id)
    return compute_full_sku(product.sku, variant.sku_suffix) if variant is not None else None


async def _push_now(session: AsyncSession, product_id: int, variant_id: int | None) -> None:
    max_sellable = await _resolve_max_sellable(session, product_id, variant_id)
    if max_sellable is None:
        return

    variant_filter = Listing.variant_id.is_(None) if variant_id is None else Listing.variant_id == variant_id
    result = await session.execute(
        select(Listing).where(
            Listing.product_id == product_id, variant_filter, Listing.platform.in_(_PUSH_ENABLED_PLATFORMS)
        )
    )
    for listing in result.scalars():
        await _push_one(session, listing, max_sellable)


async def _push_one(session: AsyncSession, listing: Listing, qty: int) -> None:
    if listing.external_listing_id is None:
        return  # no known live listing to push to (never checked, or the SKU check found none)

    connection = (
        await session.execute(select(PlatformConnection).where(PlatformConnection.platform == listing.platform))
    ).scalar_one_or_none()
    if connection is None or not connection.is_connected:
        return  # not connected — nothing to push to, and not a failure worth logging

    sku = await _resolve_sku(session, listing)
    listing_ref = ExternalListingRef(
        external_listing_id=listing.external_listing_id,
        title=listing.external_title or "",
        sku=sku,
        state=listing.external_state or "unknown",
        quantity=listing.external_quantity or 0,
        variation=listing.external_variation,
    )

    try:
        adapter = await get_adapter(session, listing.platform)
        await adapter.push_listing_quantity(session, connection, listing_ref, sku, qty)
    except Exception as e:
        logger.warning(
            "Listing push failed for product_id=%s variant_id=%s platform=%s: %s",
            listing.product_id,
            listing.variant_id,
            listing.platform.value,
            e,
        )
        session.add(
            PlatformListingPush(
                product_id=listing.product_id,
                variant_id=listing.variant_id,
                platform=listing.platform,
                attempted_qty=qty,
                status=ListingPushStatus.error,
                error_message=str(e)[:2000],
            )
        )
        await session.commit()
        return

    listing.last_synced_qty = qty
    listing.last_synced_at = datetime.now(timezone.utc)
    session.add(
        PlatformListingPush(
            product_id=listing.product_id,
            variant_id=listing.variant_id,
            platform=listing.platform,
            attempted_qty=qty,
            status=ListingPushStatus.success,
        )
    )
    await session.commit()
