from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.listing_adoption import (
    AdoptListingResult,
    UnitAdoptionResult,
    VariationMappingEntry,
    VariationMappingProposal,
)
from app.services import listing_sync
from app.services.platforms.base import ClassicListingCandidate, UnadoptedListingCandidate
from app.services.variants import compute_full_sku

__all__ = [
    "propose_variation_mapping",
    "plan_sku_alignment",
    "apply_adoption",
    "known_stocksmith_skus",
    "find_unadopted_listings",
]


async def known_stocksmith_skus(session: AsyncSession) -> set[str]:
    """Every SKU StockSmith considers its own — active products' SKUs plus each active
    variant's computed full SKU. This is the set a marketplace listing is checked
    against to decide whether it's "unadopted"."""
    products = (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars().all()
    variants = (
        (await session.execute(select(ProductVariant).where(ProductVariant.is_active.is_(True)))).scalars().all()
    )
    variants_by_product: dict[int, list[ProductVariant]] = {}
    for variant in variants:
        variants_by_product.setdefault(variant.product_id, []).append(variant)

    skus: set[str] = set()
    for product in products:
        product_variants = variants_by_product.get(product.id, [])
        if not product_variants:
            if product.sku:
                skus.add(product.sku)
            continue
        for variant in product_variants:
            full = compute_full_sku(product.sku, variant.sku_suffix)
            if full:
                skus.add(full)
    return skus


def find_unadopted_listings(
    listings: list[UnadoptedListingCandidate], known_skus: set[str]
) -> list[UnadoptedListingCandidate]:
    """Listings with at least one live product whose SKU StockSmith doesn't recognise —
    including products with no SKU at all, which are the most common real case (a
    listing created directly in Etsy's own editor).

    A listing is reported if ANY of its products is unrecognised, not only if all are:
    a part-migrated listing where one variation was linked and three weren't is exactly
    the gap worth surfacing, and filtering on "all" would hide it."""
    unadopted = []
    for listing in listings:
        if any(product.sku is None or product.sku not in known_skus for product in listing.products):
            unadopted.append(listing)
    return unadopted


def _variant_attributes(product: Product, variant: ProductVariant) -> dict[str, str]:
    pairs = [
        (product.variant_attribute1_name, variant.attribute1_value),
        (product.variant_attribute2_name, variant.attribute2_value),
        (product.variant_attribute3_name, variant.attribute3_value),
    ]
    return {name: value for name, value in pairs if name and value}


def propose_variation_mapping(
    product: Product, active_variants: list[ProductVariant], candidate: ClassicListingCandidate
) -> VariationMappingProposal:
    """Proposes a variant->eBay-SKU mapping for a selected classic listing, matched by
    attribute value where possible so a human only has to confirm/correct it rather than
    build it from scratch. Never guesses silently past "exact" — ambiguous or
    mismatched-count cases come back as "count_only"/"unmatched" for the picker's
    variation-mapping step to force a manual choice on."""
    ebay_specifics = candidate.variation_specifics

    if not active_variants:
        sku = candidate.skus[0] if len(candidate.skus) == 1 else None
        confidence = "exact" if sku else "unmatched"
        return VariationMappingProposal(
            entries=[
                VariationMappingEntry(
                    variant_id=None,
                    variant_name=None,
                    stockssmith_attributes={},
                    matched_sku=sku,
                    matched_variation_specifics=None,
                    match_confidence=confidence,
                )
            ]
        )

    if not ebay_specifics or len(ebay_specifics) != len(active_variants):
        # Count mismatch (or no per-variation specifics at all) — nothing to match
        # against, force a manual pick for every variant.
        return VariationMappingProposal(
            entries=[
                VariationMappingEntry(
                    variant_id=v.id,
                    variant_name=v.variant_name,
                    stockssmith_attributes=_variant_attributes(product, v),
                    matched_sku=None,
                    matched_variation_specifics=None,
                    match_confidence="unmatched",
                )
                for v in active_variants
            ]
        )

    remaining_specifics = list(zip(candidate.skus, ebay_specifics))
    entries: list[VariationMappingEntry] = []
    unmatched_variants: list[ProductVariant] = []

    for variant in active_variants:
        attrs = _variant_attributes(product, variant)
        match = None
        if attrs:
            for sku, specifics in remaining_specifics:
                normalized_specifics = {k.lower(): v.lower() for k, v in specifics.items()}
                if all(normalized_specifics.get(name.lower()) == value.lower() for name, value in attrs.items()):
                    match = (sku, specifics)
                    break
        if match is not None:
            remaining_specifics.remove(match)
            entries.append(
                VariationMappingEntry(
                    variant_id=variant.id,
                    variant_name=variant.variant_name,
                    stockssmith_attributes=attrs,
                    matched_sku=match[0],
                    matched_variation_specifics=match[1],
                    match_confidence="exact",
                )
            )
        else:
            unmatched_variants.append(variant)

    # Anything left over: counts matched overall, but attribute values didn't line up
    # cleanly (ambiguous eBay specifics, or a StockSmith variant with no attributes set)
    # — fall back to a positional pairing as a starting point, flagged for manual review.
    for variant, (sku, specifics) in zip(unmatched_variants, remaining_specifics):
        entries.append(
            VariationMappingEntry(
                variant_id=variant.id,
                variant_name=variant.variant_name,
                stockssmith_attributes=_variant_attributes(product, variant),
                matched_sku=sku,
                matched_variation_specifics=specifics,
                match_confidence="count_only",
            )
        )

    return VariationMappingProposal(entries=entries)


def plan_sku_alignment(
    product: Product,
    active_variants: list[ProductVariant],
    candidate: ClassicListingCandidate,
    variation_mapping: list[tuple[int | None, str]],
) -> list[str] | None:
    """Builds the SKU list to send to EbayAdapter.revise_listing_skus, positionally
    aligned with candidate.variation_specifics, or None when nothing needs changing.

    Returning None for a no-op matters: revising a listing is a real edit to live
    marketplace data, so it must not fire when every SKU already matches. Pure so the
    "which listing edits would we make" decision is testable without touching eBay."""
    variants_by_id = {v.id: v for v in active_variants}
    expected_by_ebay_sku: dict[str, str] = {}
    for variant_id, actual_sku in variation_mapping:
        variant = variants_by_id.get(variant_id) if variant_id is not None else None
        expected = compute_full_sku(product.sku, variant.sku_suffix if variant else None)
        if expected:
            expected_by_ebay_sku[actual_sku] = expected

    if candidate.variation_specifics is None:
        current = candidate.skus[0] if candidate.skus else ""
        desired = expected_by_ebay_sku.get(current)
        if desired is None or desired == current:
            return None
        return [desired]

    # Keep every variation, changing only those the mapping covers — an omitted
    # variation would be deleted by eBay (see EbayAdapter._build_revise_skus_xml).
    desired_skus = [expected_by_ebay_sku.get(sku, sku) for sku in candidate.skus]
    return None if desired_skus == candidate.skus else desired_skus


async def apply_adoption(
    session: AsyncSession,
    product: Product,
    active_variants: list[ProductVariant],
    variation_mapping: list[tuple[int | None, str]],
    platform: ListingPlatform,
    listing_title: str,
    skus_aligned: bool = False,
    external_listing_id: str | None = None,
) -> AdoptListingResult:
    """Writes the user-confirmed variant->SKU mapping onto each unit's Listing row.
    StockSmith's own computed SKU is always the lookup key going forward (source of
    truth per the feature's requirement) — a mismatch against the eBay SKU the user
    picked is recorded as a conflict for the UI to surface, never silently adopted.

    Side effect worth knowing: listing_push._resolve_sku always recomputes and pushes
    against StockSmith's own SKU, never listing.external_listing_id directly, so a
    conflicted unit's future quantity pushes will keep failing against eBay (since no
    Inventory API object exists at the StockSmith-expected SKU yet) and surface as
    errors in the existing PlatformListingPush log — exactly the visible signal a v1,
    flag-only conflict needs, with no new logging mechanism required. Once the user
    renames the SKU on eBay to match (or align_skus does it pre-migration), pushes
    start succeeding with no further change here.

    `external_listing_id` exists because the two adapters mean different things by that
    column, and writing the wrong one would break the very sync check this is meant to
    fix. eBay's _index_inventory_item sets it to the SKU (its Inventory API is
    SKU-keyed); Etsy's _index_listing_skus sets it to the listing id. Leave it None for
    eBay to get the per-unit SKU; pass the listing id for Etsy."""
    variants_by_id = {v.id: v for v in active_variants}
    now = datetime.now(timezone.utc)
    units: list[UnitAdoptionResult] = []

    for variant_id, actual_sku in variation_mapping:
        variant = variants_by_id.get(variant_id) if variant_id is not None else None
        expected_sku = compute_full_sku(product.sku, variant.sku_suffix if variant else None)

        listing = await listing_sync._get_or_create_listing(session, product.id, variant_id, platform)
        listing.external_listing_id = external_listing_id if external_listing_id is not None else expected_sku
        # A human has just confirmed this SKU belongs to this listing, which is the
        # strongest confirmation there is — stronger than a scan match, which only says
        # the marketplace happened to hold the same string.
        if actual_sku or expected_sku:
            listing.published_sku = actual_sku or expected_sku
        listing.external_title = listing_title
        listing.external_state = "active"
        listing.last_checked_at = now

        units.append(
            UnitAdoptionResult(
                variant_id=variant_id,
                sku_conflict=expected_sku != actual_sku,
                expected_sku=expected_sku,
                actual_sku=actual_sku,
            )
        )

    await session.commit()
    summary = await listing_sync.get_stored_product_sync_status(session, product.id, platform)
    return AdoptListingResult(summary=summary, units=units, skus_aligned=skus_aligned)
