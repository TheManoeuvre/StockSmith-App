from typing import Literal

from pydantic import BaseModel

from app.schemas.listing import ProductListingSyncSummary


class EligibilityAnnotatedCandidate(BaseModel):
    external_listing_id: str
    title: str
    listing_type: str
    skus: list[str]
    variation_specifics: list[dict[str, str]] | None
    quantity: int
    ineligibility_reasons: list[str]
    # False when this came from the bulk list view, whose SKU/variation data is
    # incomplete — the UI shows SKUs as "checked on selection" rather than as fact, and
    # the real detail is fetched when the user picks this listing.
    detail_loaded: bool = False


class UnmigratedListingsReport(BaseModel):
    total_count: int
    eligible_count: int
    listings: list[EligibilityAnnotatedCandidate]


class VariationMappingEntry(BaseModel):
    variant_id: int | None  # None only for a no-variant product's single unit
    variant_name: str | None
    stockssmith_attributes: dict[str, str]
    matched_sku: str | None
    matched_variation_specifics: dict[str, str] | None
    match_confidence: Literal["exact", "count_only", "unmatched"]


class VariationMappingProposal(BaseModel):
    entries: list[VariationMappingEntry]


class VariationMappingChoice(BaseModel):
    """One user-confirmed variant->SKU pairing, sent back on adopt. A dict keyed by
    variant_id can't round-trip None as a JSON object key, so this is a list of pairs
    instead of VariationMappingProposal's dict-shaped equivalent."""

    variant_id: int | None
    sku: str


class AdoptListingRequest(BaseModel):
    external_listing_id: str
    listing_title: str | None = None
    # Required whenever the listing has variations (or more than one SKU); the frontend
    # always sends the user-confirmed mapping (pre-filled from VariationMappingProposal,
    # editable) — the backend never re-derives a mapping itself, it only writes what
    # it's given.
    variation_mapping: list[VariationMappingChoice]
    # When True, any eBay SKU that differs from StockSmith's own computed SKU is
    # rewritten on eBay (before migration) so StockSmith stays the source of truth.
    # Defaults False: this edits a live listing, so it is opt-in per adoption rather
    # than something that happens silently as a side effect of linking.
    align_skus: bool = False


class UnitAdoptionResult(BaseModel):
    variant_id: int | None
    sku_conflict: bool
    expected_sku: str | None
    actual_sku: str | None


class AdoptListingResult(BaseModel):
    summary: ProductListingSyncSummary
    units: list[UnitAdoptionResult]
    # True when align_skus was requested and the eBay listing was actually revised, so
    # the UI can say the conflict was resolved rather than merely reported.
    skus_aligned: bool = False


# --- Etsy: listings with no StockSmith equivalent ----------------------------------
#
# The mirror image of the eBay case above. On eBay the product exists in StockSmith and
# the marketplace API can't see the listing; here the listing is perfectly visible and
# it's StockSmith that has no matching SKU. Same picker shape, no migration step.


class UnadoptedListingProduct(BaseModel):
    index: int
    sku: str | None
    variation: str | None
    quantity: int


class UnadoptedListing(BaseModel):
    external_listing_id: str
    title: str
    state: str
    products: list[UnadoptedListingProduct]


class UnadoptedListingsReport(BaseModel):
    total_count: int
    listings: list[UnadoptedListing]


class EtsyLinkChoice(BaseModel):
    """One user-confirmed pairing of an Etsy listing product (by its position in the
    listing) to a StockSmith variant."""

    variant_id: int | None
    product_index: int


class EtsyAdoptListingRequest(BaseModel):
    external_listing_id: str
    listing_title: str | None = None
    links: list[EtsyLinkChoice]
    # Etsy has no migration step, so linking is only meaningful if the listing ends up
    # carrying StockSmith's SKU — otherwise the next sync check would report it missing
    # all over again. Defaults True (the opposite of eBay's align_skus, where the
    # equivalent edit is avoidable because migration can carry correct SKUs across).
    write_skus: bool = True
