from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.kitting import VariantKittingBomLine
from app.schemas.material_substitute import SubstituteSuggestion
from app.schemas.product import BomLine, PlatformConflictResolution


class VariantBase(BaseModel):
    variant_name: str
    sku_suffix: str | None = None


class VariantCreate(VariantBase):
    # Not a column: popped by the router before the ORM row is built. See
    # services/variant_platform_conflicts for the 409 it controls.
    on_platform_conflict: PlatformConflictResolution = "ask"


class VariantUpdate(BaseModel):
    variant_name: str | None = None
    sku_suffix: str | None = None
    is_active: bool | None = None
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None
    # Consulted only when is_active flips to true — reactivating is the third way the
    # active count can pass a platform's cap. Popped by the router, never set on the row.
    on_platform_conflict: PlatformConflictResolution = "ask"


class VariantPricingBulkUpdate(BaseModel):
    """One write for every variant in a pricing group. The variable-pricing form used to
    PATCH each variant on its own, which for a product with hundreds of variants meant
    hundreds of concurrent requests and an exhausted connection pool."""

    variant_ids: list[int]
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None


class VariantBomLine(BomLine):
    replaces_material_id: int | None = None
    # How many units this line alone allows from the material's own stock, now and once
    # open purchase orders land...
    line_max_buildable: int | None = None
    line_expected_max_buildable: int | None = None
    # ...and the same once the material's active fallbacks are pooled in (see
    # material_substitutes.FALLBACK_POOL_BY_MATERIAL_SQL). max_buildable is the min() of
    # the first pair, max_buildable_incl_fallbacks of the second.
    line_max_buildable_incl_fallbacks: int | None = None
    line_expected_max_buildable_incl_fallbacks: int | None = None
    # Populated only when line_max_buildable == 0 — the material's own shelf can't build
    # a unit, whether or not a fallback is covering it — ranked, human-curated fallbacks
    # for material_id, surfaced so a person can choose one. Never auto-applied; see
    # app.models.material_substitute.
    suggested_substitutes: list[SubstituteSuggestion] = []


class VariantRead(VariantBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    is_active: bool
    current_stock: int
    allocated_qty: int = 0
    attribute1_value: str | None = None
    attribute2_value: str | None = None
    attribute3_value: str | None = None
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None
    effective_platform_fee_percent: Decimal | None = None
    effective_shipping_profile_id: int | None = None
    # From the BOM's own materials only, vs. counting each material's active fallbacks —
    # the sellable figures below are built on the latter. See buildability.BuildableFigures.
    max_buildable: int | None = None
    expected_max_buildable: int | None = None
    max_buildable_incl_fallbacks: int | None = None
    expected_max_buildable_incl_fallbacks: int | None = None
    max_sellable: int | None = None
    max_sellable_reason: str | None = None
    expected_max_sellable: int | None = None
    expected_max_sellable_reason: str | None = None
    theoretical_max_sellable: int | None = None
    theoretical_max_sellable_reason: str | None = None
    cost_per_unit: Decimal | None = None
    # Resolved kitting BOM cost per unit (variant overrides applied) — see ProductRead's own.
    kitting_cost_per_unit: Decimal | None = None
    effective_bom: list[VariantBomLine] = []
    effective_kitting_bom: list[VariantKittingBomLine] = []
    full_sku: str | None = None


# Which variant's BOM (or kitting) overrides the merged variant keeps when the two differ.
# There is no third option: a merge cannot combine two recipes, only pick one.
MergeBomChoice = Literal["keep_survivor", "take_loser"]

# What a merge does when the variant being merged away is live on a marketplace: "ask"
# refuses with a 409 listing the listings so the client can confirm, "proceed" pushes a
# zero quantity to each and carries on.
LiveListingResolution = Literal["ask", "proceed"]


class VariantMergePreviewRequest(BaseModel):
    target_id: int  # the survivor


class VariantMergeRequest(VariantMergePreviewRequest):
    bom: MergeBomChoice = "keep_survivor"
    kitting: MergeBomChoice = "keep_survivor"
    on_live_listing: LiveListingResolution = "ask"


class MergeBomLine(BaseModel):
    """One line of an effective BOM, named for display — a preview shows two of these side
    by side so the user can see what "take loser's" would actually change."""

    material_id: int
    material_name: str
    qty_required: Decimal
    replaces_material_id: int | None = None
    replaces_material_name: str | None = None


class MergeOpenLine(BaseModel):
    order_id: int
    order_reference: str | None
    qty: int  # units not yet shipped — what will move to the survivor


class MergeLiveListing(BaseModel):
    platform: str
    published_sku: str | None
    external_listing_id: str


class VariantMergeUnit(BaseModel):
    id: int
    variant_name: str
    full_sku: str | None
    is_active: bool
    current_stock: int
    allocated_qty: int


class VariantMergePlan(BaseModel):
    """Everything a merge would do, computed without doing it."""

    loser: VariantMergeUnit
    survivor: VariantMergeUnit
    stock_to_move: int
    open_lines: list[MergeOpenLine]
    bom_differs: bool
    kitting_differs: bool
    loser_bom: list[MergeBomLine]
    survivor_bom: list[MergeBomLine]
    loser_kitting: list[MergeBomLine]
    survivor_kitting: list[MergeBomLine]
    live_listings: list[MergeLiveListing]
    # Conditions that stop the merge outright, in user-facing words. Non-empty means the
    # apply endpoint will refuse with the same message.
    blockers: list[str]


class VariantMergeResult(BaseModel):
    survivor: VariantRead
    stock_moved: int
    open_lines_moved: int
    # Push failures are reported, not fatal: the merge has happened, the marketplace side
    # needs a hand.
    warnings: list[str]
