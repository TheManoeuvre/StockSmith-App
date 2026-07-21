from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class KittingBomLine(BaseModel):
    material_id: int
    qty_required: Decimal


class KittingBomLineRead(KittingBomLine):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int


class VariantKittingBomLine(KittingBomLine):
    replaces_material_id: int | None = None
    line_max_buildable: int | None = None
    line_expected_max_buildable: int | None = None


class OrderKittingOverrideLine(BaseModel):
    material_id: int
    qty_required: Decimal
    replaces_material_id: int | None = None


class OrderKittingRequirementLine(BaseModel):
    material_id: int
    material_name: str
    auto_qty: Decimal
    effective_qty: Decimal
    reserved_qty: Decimal
    consumed_qty: Decimal


class OrderKittingSummary(BaseModel):
    overrides: list[OrderKittingOverrideLine]
    lines: list[OrderKittingRequirementLine]
