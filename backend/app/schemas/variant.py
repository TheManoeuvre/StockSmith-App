from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.kitting import VariantKittingBomLine
from app.schemas.product import BomLine


class VariantBase(BaseModel):
    variant_name: str
    sku_suffix: str | None = None


class VariantCreate(VariantBase):
    pass


class VariantUpdate(BaseModel):
    variant_name: str | None = None
    sku_suffix: str | None = None
    is_active: bool | None = None
    sale_price: Decimal | None = None
    shipping_profile_id: int | None = None
    platform_fee_percent: Decimal | None = None


class VariantBomLine(BomLine):
    replaces_material_id: int | None = None
    line_max_buildable: int | None = None
    line_expected_max_buildable: int | None = None


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
    max_buildable: int | None = None
    expected_max_buildable: int | None = None
    max_sellable: int | None = None
    max_sellable_reason: str | None = None
    expected_max_sellable: int | None = None
    expected_max_sellable_reason: str | None = None
    theoretical_max_sellable: int | None = None
    theoretical_max_sellable_reason: str | None = None
    cost_per_unit: Decimal | None = None
    effective_bom: list[VariantBomLine] = []
    effective_kitting_bom: list[VariantKittingBomLine] = []
    full_sku: str | None = None
