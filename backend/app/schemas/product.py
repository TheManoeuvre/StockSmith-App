from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.product import PricingMode


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
    pricing_mode: PricingMode | None = None
    pricing_variable_attribute: int | None = None


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
    cost_per_unit: Decimal | None = None
    main_image_asset_id: int | None = None
    ready_to_ship: int | None = None
    effective_platform_fee_percent: Decimal | None = None


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


class ProductPriceSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    cost_per_unit: Decimal
    sale_price: Decimal | None
    margin_percent: Decimal | None
    recorded_at: datetime
