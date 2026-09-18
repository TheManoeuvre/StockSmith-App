from decimal import Decimal

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
