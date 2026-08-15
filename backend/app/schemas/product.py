from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.abc_classification import ABCClass
from app.models.product import PricingMode
from app.schemas.abc import ResolvedClassificationRead


class ProductBase(BaseModel):
    name: str
    sku: str | None = None
    description: str | None = None
    barcode: str | None = None
    is_bundle: bool = False
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None
    platform_ceiling_qty: int | None = None
    push_buildable_capacity: bool = True
    product_type_id: int | None = None
    # NULL means "inherit" for both — see services/abc.py. A product's tier and cadence
    # cover all of its variants; last_stock_take_at is per stock-holding row and is written
    # only by an approved stock take, never by editing the product.
    abc_class: ABCClass | None = None
    stock_take_interval_days: int | None = Field(default=None, gt=0)


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = None
    sku: str | None = None
    description: str | None = None
    barcode: str | None = None
    is_active: bool | None = None
    is_bundle: bool | None = None
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None
    platform_ceiling_qty: int | None = None
    push_buildable_capacity: bool | None = None
    pricing_mode: PricingMode | None = None
    pricing_variable_attribute: int | None = None
    product_type_id: int | None = None
    abc_class: ABCClass | None = None
    stock_take_interval_days: int | None = Field(default=None, gt=0)


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    current_stock: int
    allocated_qty: int = 0
    variant_attribute1_name: str | None = None
    variant_attribute2_name: str | None = None
    variant_attribute3_name: str | None = None
    pricing_mode: PricingMode = PricingMode.product
    pricing_variable_attribute: int | None = None
    created_at: datetime
    updated_at: datetime
    max_buildable: int | None = None
    expected_max_buildable: int | None = None
    max_sellable: int | None = None
    max_sellable_reason: str | None = None
    expected_max_sellable: int | None = None
    expected_max_sellable_reason: str | None = None
    theoretical_max_sellable: int | None = None
    theoretical_max_sellable_reason: str | None = None
    cost_per_unit: Decimal | None = None
    # Base kitting BOM cost per unit — the packaging half of what it costs to fulfil one
    # unit, so margin can account for it (see pricing.compute_profit_margin). None for a
    # bundle, and for a product with no kitting BOM at all.
    kitting_cost_per_unit: Decimal | None = None
    main_image_asset_id: int | None = None
    ready_to_ship: int | None = None
    effective_platform_fee_percent: Decimal | None = None
    product_type_name: str | None = None
    last_stock_take_at: datetime | None = None
    # See MaterialRead.classification — same reasoning, same population rule.
    classification: ResolvedClassificationRead | None = None


class ProductPage(BaseModel):
    items: list[ProductRead]
    total: int


class BomLine(BaseModel):
    material_id: int
    qty_required: Decimal


class BomLineRead(BomLine):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int


class BundleItem(BaseModel):
    component_product_id: int
    qty: int


class BundleItemRead(BundleItem):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bundle_product_id: int


class AttributeMaterialRule(BaseModel):
    """Says an attribute's value should substitute a different material on one base BOM
    line — e.g. Colour's value "Blue" substitutes the base filament for "Blue PLA"."""

    base_material_id: int
    value_to_material_id: dict[str, int]


class AttributeQuantityRule(BaseModel):
    """Says an attribute's value should override the qty_required on one base BOM line —
    e.g. Size's value "Large" needs 2x the filament of the base line."""

    base_material_id: int
    value_to_qty: dict[str, Decimal]


class VariantAttributeSpec(BaseModel):
    name: str
    values: list[str]
    material_rules: list[AttributeMaterialRule] = []
    quantity_rules: list[AttributeQuantityRule] = []


class GenerateVariantsRequest(BaseModel):
    attributes: list[VariantAttributeSpec]


class BulkBomAmendLine(BaseModel):
    """One base BOM line to amend, in the same vocabulary as the generation-time rules —
    "for this attribute value, this base line becomes material M at quantity Q". That's
    exactly what the user configured originally and now wants to correct, so it's a shape
    they already have a mental model for."""

    base_material_id: int
    material_id: int | None = None  # substitution target; None keeps the base material
    qty_required: Decimal | None = None  # None keeps the base BOM quantity


class BulkBomAmendRequest(BaseModel):
    attribute_name: str  # matched against product.variant_attribute{1,2,3}_name
    attribute_value: str  # e.g. "Large"
    lines: list[BulkBomAmendLine]
    # Preview by default. This touches an unbounded number of variants and can overwrite
    # hand edits it has no way to distinguish from rule-generated ones, and the user is
    # already correcting a mistake — so showing the change before making it is the
    # default, not an option.
    apply: bool = False
    include_inactive: bool = False


class BulkBomAmendChange(BaseModel):
    base_material_id: int
    base_material_name: str
    before_material_id: int | None  # None = inherited the base BOM (no override row)
    before_qty: Decimal | None
    after_material_id: int | None  # None = the amend removes the override
    after_qty: Decimal | None


class BulkBomAmendUnit(BaseModel):
    variant_id: int
    variant_name: str
    changes: list[BulkBomAmendChange]  # empty when this variant is already correct


class BulkBomAmendResult(BaseModel):
    applied: bool
    matched_variant_count: int
    changed_variant_count: int
    skipped_inactive_count: int
    units: list[BulkBomAmendUnit]


class ProductPriceSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    cost_per_unit: Decimal
    sale_price: Decimal | None
    margin_percent: Decimal | None
    recorded_at: datetime
