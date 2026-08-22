"""Building a draft listing from StockSmith's own data, and pushing it.

The first thing in this codebase that creates something on a marketplace. Every other
outbound call updates an object that already exists — a quantity, a set of SKUs, a
migration — so the failure modes here are new: an Etsy draft cannot be deleted through this
app, and a duplicate is not recoverable from here.

Three rules follow from that:

  * **Re-derive, never trust the preview.** The caller sends nothing but a product id. What
    the user looked at may be minutes old, and this writes to their shop. Same reasoning as
    push_product_corrections recomputing its quantities at send time.
  * **Refuse rather than guess.** If readiness reports a blocker, no call is made. Taxonomy,
    policies and condition determine categorisation, tax treatment and fee band, so a
    plausible default is how a seller ends up paying the wrong final-value fee.
  * **One at a time, per product.** A double-click that creates two drafts leaves one that
    nobody can remove without opening Etsy.

Publishing is deliberately not possible from here. The draft is a starting point the seller
finishes in the marketplace's own editor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetType, ProductAsset
from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import draft_readiness, file_storage, listing_copy, listing_profiles, listing_push
from app.services.general_settings import get_general_settings
from app.services.platforms.base import DraftImage, DraftListing, DraftUnit
from app.services.variants import compute_full_sku

# One push at a time per (platform, product). The window between "is there already a
# listing?" and "there is now" is a real HTTP round trip, and two clicks inside it would
# create two drafts. Mirrors the per-listing locking listing_push already does.
_locks: dict[tuple[ListingPlatform, int], asyncio.Lock] = {}


def _lock_for(platform: ListingPlatform, product_id: int) -> asyncio.Lock:
    key = (platform, product_id)
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


class DraftPushError(Exception):
    """Refused before anything was sent. Carries a sentence meant for the user."""


@dataclass
class DraftPushResult:
    external_listing_id: str | None
    state: str
    units_linked: int
    warnings: list[str] = field(default_factory=list)
    publish_blockers: list[str] = field(default_factory=list)


# Profile fields that map onto the neutral metadata bag the adapters read.
_ETSY_METADATA = {
    "etsy.taxonomy_id": "etsy_taxonomy_id",
    "etsy.who_made": "etsy_who_made",
    "etsy.when_made": "etsy_when_made",
    "etsy.is_supply": "etsy_is_supply",
    "etsy.shipping_profile_id": "etsy_shipping_profile_id",
    "etsy.readiness_state_id": "etsy_readiness_state_id",
    "etsy.return_policy_id": "etsy_return_policy_id",
    "etsy.shop_section_id": "etsy_shop_section_id",
}

_EBAY_METADATA = {
    "ebay.category_id": "ebay_category_id",
    "ebay.condition": "ebay_condition",
    "ebay.fulfillment_policy_id": "ebay_fulfillment_policy_id",
    "ebay.payment_policy_id": "ebay_payment_policy_id",
    "ebay.return_policy_id": "ebay_return_policy_id",
    "ebay.merchant_location_key": "ebay_merchant_location_key",
    "ebay.marketplace_id": "ebay_marketplace_id",
}


async def build_draft(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> DraftListing:
    """Assembles the payload. Raises DraftPushError if readiness says it can't be built.

    The omit rule lives here: an optional field with no value contributes no key at all —
    never an empty string, never a zero. A required field with no value is a blocker, and
    readiness has already said so by the time this runs.
    """
    readiness = await draft_readiness.evaluate(session, product_id, platform)
    if readiness is None:
        raise DraftPushError("Product not found")
    if not readiness.can_create:
        first = readiness.blockers[0]
        raise DraftPushError(first.message)

    product = await session.get(Product, product_id)
    variants = list(
        (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)
                )
            )
        ).scalars()
    )

    settings = await listing_copy.get_settings(session, product_id, platform)
    copy = listing_copy.resolve_copy(product, settings)
    profile = await listing_profiles.resolve_profile(session, product_id, platform)

    mapping = _ETSY_METADATA if platform == ListingPlatform.etsy else _EBAY_METADATA
    metadata = {}
    for key, attribute in mapping.items():
        value = getattr(profile, attribute, None) if profile else None
        if value is not None:
            metadata[key] = value

    attribute_names = [
        name
        for name in (
            product.variant_attribute1_name,
            product.variant_attribute2_name,
            product.variant_attribute3_name,
        )
        if name
    ]

    units: list[DraftUnit] = []
    for variant in variants or [None]:
        price = None
        if variant is not None:
            price = variant.sale_price if variant.sale_price is not None else product.sale_price
        else:
            price = product.sale_price
        # A unit with no price is left out rather than failing the whole push — the same
        # per-unit tolerance listing_sync applies. Readiness has already warned about it.
        if price is None:
            continue

        quantity = await listing_push.resolve_push_quantity(
            session, product_id, variant.id if variant else None
        )
        attributes = {}
        if variant is not None:
            values = [variant.attribute1_value, variant.attribute2_value, variant.attribute3_value]
            attributes = {n: v for n, v in zip(attribute_names, values) if n and v}

        units.append(
            DraftUnit(
                sku=compute_full_sku(product.sku, variant.sku_suffix) if variant else product.sku,
                price=f"{price:.2f}",
                quantity=max(quantity or 0, 0),
                attributes=attributes,
                variant_id=variant.id if variant else None,
            )
        )

    images: list[DraftImage] = []
    hero = (
        await session.execute(
            select(ProductAsset)
            .where(ProductAsset.product_id == product_id, ProductAsset.asset_type == AssetType.main_image)
            .order_by(ProductAsset.display_order, ProductAsset.id)
        )
    ).scalars().first()
    if hero is not None:
        try:
            path = file_storage.resolve_asset_path(hero.file_path)
            images.append(DraftImage(data=path.read_bytes(), filename=hero.original_filename, rank=1))
        except OSError:
            # A missing file on disk is a publish blocker, not a reason to refuse — the
            # adapter reports it and the draft is still worth having.
            pass

    general = await get_general_settings(session)
    return DraftListing(
        title=copy.title,
        description=copy.description or "",
        currency=general.default_currency.value,
        attribute_names=attribute_names,
        units=units,
        images=images,
        metadata=metadata,
    )


async def push_draft(
    session: AsyncSession,
    adapter,
    connection: PlatformConnection,
    product_id: int,
    platform: ListingPlatform,
) -> DraftPushResult:
    """Creates the draft and records the link, under a per-product lock."""
    async with _lock_for(platform, product_id):
        # Re-checked inside the lock: the whole point is the window between this check and
        # the marketplace responding.
        existing = (
            await session.execute(
                select(Listing).where(
                    Listing.product_id == product_id,
                    Listing.platform == platform,
                    Listing.external_listing_id.is_not(None),
                )
            )
        ).scalars().first()
        if existing is not None:
            raise DraftPushError(
                "This product is already linked to a listing on this platform. "
                "Creating another would leave a duplicate that can't be removed from here."
            )

        draft = await build_draft(session, product_id, platform)
        result = await adapter.create_draft_listing(session, connection, draft)

        # Written from the response rather than left for the next sync check to discover.
        # EtsyAdapter.build_listing_sku_index does not ask for draft listings, so a draft
        # would otherwise be invisible to the very check that offered to create it.
        now = datetime.now(timezone.utc)
        linked = 0
        for unit in draft.units:
            key = str(unit.variant_id) if unit.variant_id is not None else ""
            external_id = result.unit_refs.get(key, result.external_listing_id)
            if external_id is None:
                continue
            listing = (
                await session.execute(
                    select(Listing).where(
                        Listing.product_id == product_id,
                        Listing.variant_id == unit.variant_id
                        if unit.variant_id is not None
                        else Listing.variant_id.is_(None),
                        Listing.platform == platform,
                    )
                )
            ).scalar_one_or_none()
            if listing is None:
                listing = Listing(product_id=product_id, variant_id=unit.variant_id, platform=platform)
                session.add(listing)
            listing.external_listing_id = external_id
            listing.external_title = draft.title
            listing.external_state = result.state
            listing.external_quantity = unit.quantity
            listing.last_checked_at = now
            if unit.sku:
                listing.published_sku = unit.sku
            linked += 1

        await session.commit()
        return DraftPushResult(
            external_listing_id=result.external_listing_id,
            state=result.state,
            units_linked=linked,
            warnings=result.warnings,
            publish_blockers=result.publish_blockers,
        )
