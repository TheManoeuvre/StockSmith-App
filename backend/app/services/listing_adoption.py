from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant
from app.schemas.listing_adoption import (
    AdoptListingResult,
    AttributePair,
    EtsyVariationMappingEntry,
    EtsyVariationMappingProposal,
    UnitAdoptionResult,
    VariationMappingEntry,
    VariationMappingProposal,
)
from app.services import listing_sync
from app.services.platforms.base import ClassicListingCandidate, ListingProductRef, UnadoptedListingCandidate
from app.services.variants import compute_full_sku

__all__ = [
    "match_variations",
    "normalise_token",
    "propose_variation_mapping",
    "propose_etsy_variation_mapping",
    "UnknownPlatformAttribute",
    "plan_sku_alignment",
    "SkuAlignmentPlan",
    "apply_adoption",
    "known_stocksmith_skus",
    "find_unadopted_listings",
]


@dataclass
class SkuAlignmentPlan:
    """What EbayAdapter.revise_listing_skus should send before migration.

    `variation_skus` is the full positional list (length 1 for a single-SKU listing) —
    every variation is always included because eBay deletes any it isn't told about.
    `listing_sku` is the listing-level Item.SKU to set: None for a single-SKU listing
    (there it is just variation_skus[0]), and for a multi-variation listing None means
    "leave whatever Item.SKU is already there untouched".
    """

    variation_skus: list[str]
    listing_sku: str | None = None


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


def normalise_token(value: str) -> str:
    """Case-folds and drops everything that isn't a letter or digit, so "Red / Blue",
    "red-blue" and "RED BLUE" all compare equal. Used for both attribute names and
    values — marketplaces and sellers punctuate them every which way."""
    return "".join(ch for ch in value.casefold() if ch.isalnum())


# Spelling variants folded together *after* normalisation, for attribute names only.
# Kept tiny on purpose: anything beyond obvious spellings is better handled by the
# value-overlap inference in _pair_attribute_names than by a growing synonym list.
_NAME_ALIASES = {"color": "colour", "colors": "colour", "colours": "colour"}


def _normalise_name(name: str) -> str:
    token = normalise_token(name)
    return _NAME_ALIASES.get(token, token)


@dataclass
class PlatformVariation:
    """One variation of a marketplace listing, platform-neutral: `key` is whatever the
    platform's adopt request addresses it by (eBay: the variation SKU; Etsy: the
    product's index), `attributes` its {name: value} specifics."""

    key: str | int
    attributes: dict[str, str]
    display: str | None = None


@dataclass
class AttributePairing:
    stocksmith_name: str
    platform_name: str | None
    source: Literal["exact", "inferred", "manual", "unmatched"]


MatchConfidence = Literal["exact", "count_only", "unmatched"]


@dataclass
class VariantMatch:
    variant: ProductVariant | None
    matched: PlatformVariation | None
    confidence: MatchConfidence


@dataclass
class MatchOutcome:
    attribute_pairs: list[AttributePairing] = field(default_factory=list)
    platform_attribute_names: list[str] = field(default_factory=list)
    matches: list[VariantMatch] = field(default_factory=list)


class UnknownPlatformAttribute(ValueError):
    """attribute_map named a platform attribute the listing doesn't carry."""


def _pair_attribute_names(
    product: Product,
    active_variants: list[ProductVariant],
    variations: list[PlatformVariation],
    platform_names: list[str],
    attribute_map: dict[str, str | None] | None,
) -> list[AttributePairing]:
    """Stage one of matching: which platform attribute is each StockSmith attribute?
    Manual choices win, then normalised-name equality, then a platform attribute whose
    values overlap this attribute's values (the names differ but the content is plainly
    the same thing). Each platform attribute is paired at most once."""
    per_variant = [_variant_attributes(product, v) for v in active_variants]
    stocksmith_names = [
        name
        for name in (
            product.variant_attribute1_name,
            product.variant_attribute2_name,
            product.variant_attribute3_name,
        )
        if name and any(name in attrs for attrs in per_variant)
    ]
    by_normalised = {_normalise_name(name): name for name in platform_names}
    pairs: dict[str, AttributePairing] = {}
    taken: set[str] = set()

    for name in stocksmith_names:
        if attribute_map is not None and name in attribute_map:
            chosen = attribute_map[name]
            if chosen is not None and chosen not in platform_names:
                raise UnknownPlatformAttribute(chosen)
            pairs[name] = AttributePairing(name, chosen, "manual" if chosen is not None else "unmatched")
            if chosen is not None:
                taken.add(chosen)

    for name in stocksmith_names:
        if name in pairs:
            continue
        exact = by_normalised.get(_normalise_name(name))
        if exact is not None and exact not in taken:
            pairs[name] = AttributePairing(name, exact, "exact")
            taken.add(exact)

    for name in stocksmith_names:
        if name in pairs:
            continue
        ours = {normalise_token(attrs[name]) for attrs in per_variant if name in attrs}
        scores: list[tuple[int, str]] = []
        for platform_name in platform_names:
            if platform_name in taken:
                continue
            theirs = {normalise_token(v.attributes[platform_name]) for v in variations if platform_name in v.attributes}
            overlap = len(ours & theirs)
            if overlap:
                scores.append((overlap, platform_name))
        scores.sort(key=lambda entry: -entry[0])
        if scores and (len(scores) == 1 or scores[0][0] > scores[1][0]):
            pairs[name] = AttributePairing(name, scores[0][1], "inferred")
            taken.add(scores[0][1])
        else:
            pairs[name] = AttributePairing(name, None, "unmatched")

    return [pairs[name] for name in stocksmith_names]


def match_variations(
    product: Product,
    active_variants: list[ProductVariant],
    variations: list[PlatformVariation],
    attribute_map: dict[str, str | None] | None = None,
) -> MatchOutcome:
    """Proposes a StockSmith-variant -> platform-variation pairing so a human only has
    to confirm/correct it rather than build it from scratch. Two stages: pair attribute
    *names* (see _pair_attribute_names), then match each variant's values under those
    pairs, case- and symbol-insensitively.

    Never guesses silently past "exact": a variant is only paired by value when exactly
    one untaken variation fits. Whatever is left over is paired positionally as
    "count_only" when the counts happen to agree (a plausible starting point, flagged
    for review) and left "unmatched" otherwise. Attribute-exact matches survive a count
    mismatch — only the positional fallback is withheld then.

    `attribute_map` is the user's manual say on stage one: {stocksmith_name:
    platform_name | None}; names absent from it are still paired automatically."""
    if not active_variants:
        matched = variations[0] if len(variations) == 1 else None
        return MatchOutcome(matches=[VariantMatch(None, matched, "exact" if matched else "unmatched")])

    platform_names: list[str] = []
    for variation in variations:
        for name in variation.attributes:
            if name not in platform_names:
                platform_names.append(name)

    # No per-variation attributes at all (an eBay listing with no specifics): there is
    # nothing to pair names against, so don't offer the user an empty pairing step.
    pairs = (
        _pair_attribute_names(product, active_variants, variations, platform_names, attribute_map)
        if platform_names
        else []
    )
    paired = {p.stocksmith_name: p.platform_name for p in pairs if p.platform_name is not None}

    def wanted(variant: ProductVariant) -> dict[str, str]:
        attrs = _variant_attributes(product, variant)
        return {paired[name]: normalise_token(value) for name, value in attrs.items() if name in paired}

    def fits(wants: dict[str, str], variation: PlatformVariation) -> bool:
        return all(
            platform_name in variation.attributes and normalise_token(variation.attributes[platform_name]) == value
            for platform_name, value in wants.items()
        )

    matched: dict[int, tuple[PlatformVariation, MatchConfidence]] = {}
    remaining = list(variations)
    # Iterate to a fixed point: a variant that fits two variations becomes unambiguous
    # once another variant claims one of them.
    progressed = True
    while progressed:
        progressed = False
        for position, variant in enumerate(active_variants):
            if position in matched:
                continue
            wants = wanted(variant)
            if not wants:
                continue
            fitting = [v for v in remaining if fits(wants, v)]
            if len(fitting) == 1:
                matched[position] = (fitting[0], "exact")
                remaining.remove(fitting[0])
                progressed = True

    leftover = [i for i in range(len(active_variants)) if i not in matched]
    if len(variations) == len(active_variants):
        for position, variation in zip(leftover, remaining):
            matched[position] = (variation, "count_only")

    return MatchOutcome(
        attribute_pairs=pairs,
        platform_attribute_names=platform_names,
        matches=[
            VariantMatch(variant, *matched[i]) if i in matched else VariantMatch(variant, None, "unmatched")
            for i, variant in enumerate(active_variants)
        ],
    )


def _attribute_pairs(outcome: MatchOutcome) -> list[AttributePair]:
    return [
        AttributePair(stocksmith_name=p.stocksmith_name, platform_name=p.platform_name, source=p.source)
        for p in outcome.attribute_pairs
    ]


def propose_variation_mapping(
    product: Product,
    active_variants: list[ProductVariant],
    candidate: ClassicListingCandidate,
    attribute_map: dict[str, str | None] | None = None,
) -> VariationMappingProposal:
    """match_variations for a classic eBay listing, keyed by variation SKU. Without
    per-variation specifics there is nothing to match by value, so every variant lands
    on the positional/unmatched fallback."""
    specifics = candidate.variation_specifics or [{} for _ in candidate.skus]
    variations = [PlatformVariation(key=sku, attributes=attrs) for sku, attrs in zip(candidate.skus, specifics)]
    outcome = match_variations(product, active_variants, variations, attribute_map)
    return VariationMappingProposal(
        attribute_pairs=_attribute_pairs(outcome),
        platform_attribute_names=outcome.platform_attribute_names,
        entries=[
            VariationMappingEntry(
                variant_id=m.variant.id if m.variant else None,
                variant_name=m.variant.variant_name if m.variant else None,
                stockssmith_attributes=_variant_attributes(product, m.variant) if m.variant else {},
                matched_sku=str(m.matched.key) if m.matched else None,
                matched_variation_specifics=(m.matched.attributes or None) if m.matched else None,
                match_confidence=m.confidence,
            )
            for m in outcome.matches
        ],
    )


def propose_etsy_variation_mapping(
    product: Product,
    active_variants: list[ProductVariant],
    products: list[ListingProductRef],
    attribute_map: dict[str, str | None] | None = None,
) -> EtsyVariationMappingProposal:
    """match_variations for an Etsy listing, keyed by the product's position in the
    listing (what EtsyLinkChoice.product_index sends back on adopt)."""
    variations = [PlatformVariation(key=p.index, attributes=p.attributes, display=p.variation) for p in products]
    outcome = match_variations(product, active_variants, variations, attribute_map)
    return EtsyVariationMappingProposal(
        attribute_pairs=_attribute_pairs(outcome),
        platform_attribute_names=outcome.platform_attribute_names,
        entries=[
            EtsyVariationMappingEntry(
                variant_id=m.variant.id if m.variant else None,
                variant_name=m.variant.variant_name if m.variant else None,
                stockssmith_attributes=_variant_attributes(product, m.variant) if m.variant else {},
                matched_index=int(m.matched.key) if m.matched else None,
                matched_variation=m.matched.display if m.matched else None,
                matched_attributes=(m.matched.attributes or None) if m.matched else None,
                match_confidence=m.confidence,
            )
            for m in outcome.matches
        ],
    )


def plan_sku_alignment(
    product: Product,
    active_variants: list[ProductVariant],
    candidate: ClassicListingCandidate,
    variation_mapping: list[tuple[int | None, str]],
) -> SkuAlignmentPlan | None:
    """Decides the pre-migration ReviseFixedPriceItem to send, or None when nothing needs
    changing.

    Returning None for a no-op matters: revising a listing is a real edit to live
    marketplace data, so it must not fire when everything already matches. Pure so the
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
        return SkuAlignmentPlan(variation_skus=[desired])

    # Keep every variation, changing only those the mapping covers — an omitted
    # variation would be deleted by eBay (see EbayAdapter._build_revise_skus_xml).
    desired_skus = [expected_by_ebay_sku.get(sku, sku) for sku in candidate.skus]
    variations_changed = desired_skus != candidate.skus

    # The listing-level Item.SKU is a separate field a seller often leaves unset, and
    # eBay's bulkMigrateListing rejects a multi-variation listing without one. Set it
    # from the product's own SKU (the "main" SKU, no variant suffix). Only proposed as a
    # change when it actually differs; when it already matches but a variation SKU is
    # changing, the existing value is echoed back so the revise doesn't blank it.
    main_sku = product.sku or None
    listing_sku_change = main_sku if main_sku and main_sku != candidate.listing_sku else None

    if not variations_changed and listing_sku_change is None:
        return None
    return SkuAlignmentPlan(
        variation_skus=desired_skus,
        listing_sku=listing_sku_change if listing_sku_change is not None else candidate.listing_sku,
    )


async def apply_adoption(
    session: AsyncSession,
    product: Product,
    active_variants: list[ProductVariant],
    variation_mapping: list[tuple[int | None, str]],
    platform: ListingPlatform,
    listing_title: str,
    skus_aligned: bool = False,
    external_listing_id: str | None = None,
    marketplace_shipping_id: int | str | None = None,
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
    eBay to get the per-unit SKU; pass the listing id for Etsy.

    `marketplace_shipping_id` is the listing's own Etsy shipping profile id / eBay
    fulfillment policy id when the caller could read it. If a local ShippingProfile is
    linked to it and the product has no shipping profile yet, the product is pointed at
    it — the adopted listing already ships that way, so the draft/margin side should say
    so too. A shipping profile the user has already set is never overwritten."""
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

    shipping_profile_assigned = await _assign_linked_shipping_profile(
        session, product, platform, marketplace_shipping_id
    )

    await session.commit()
    summary = await listing_sync.get_stored_product_sync_status(session, product.id, platform)
    return AdoptListingResult(
        summary=summary,
        units=units,
        skus_aligned=skus_aligned,
        shipping_profile_assigned=shipping_profile_assigned,
    )


async def _assign_linked_shipping_profile(
    session: AsyncSession,
    product: Product,
    platform: ListingPlatform,
    marketplace_shipping_id: int | str | None,
) -> str | None:
    """Sets product.shipping_profile_id to the local profile linked to the listing's
    marketplace shipping id, only when the product has none. Returns the profile's name
    when it did, so the UI can say so."""
    if marketplace_shipping_id is None or product.shipping_profile_id is not None:
        return None
    if platform == ListingPlatform.etsy:
        condition = ShippingProfile.etsy_shipping_profile_id == int(marketplace_shipping_id)
    elif platform == ListingPlatform.ebay:
        condition = ShippingProfile.ebay_fulfillment_policy_id == str(marketplace_shipping_id)
    else:
        return None
    profile = (await session.execute(select(ShippingProfile).where(condition))).scalar_one_or_none()
    if profile is None:
        return None
    product.shipping_profile_id = profile.id
    return profile.name
