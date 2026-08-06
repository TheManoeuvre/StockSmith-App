from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class KittingBomLine(BaseModel):
    material_id: int
    qty_required: Decimal


class KittingBomLineRead(KittingBomLine):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int


class DefaultKittingBomLineRead(KittingBomLine):
    """A configured default-kitting-BOM line (Settings > General) — no product_id, since
    this list isn't attached to any one product; see DefaultKittingMaterial."""

    model_config = ConfigDict(from_attributes=True)

    id: int


class VariantKittingBomLine(KittingBomLine):
    replaces_material_id: int | None = None
    line_max_buildable: int | None = None
    line_expected_max_buildable: int | None = None
    # Populated only by the capacity paths, which already SELECT the material row — lets
    # kitting.kitting_cost_per_unit_from_bom cost an already-resolved BOM without re-querying.
    unit_cost: Decimal | None = None


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
    # unit_cost is frozen-at-consumption where the ledger has one (unit_cost_is_frozen), else
    # the material's live avg_unit_cost. effective_cost is the forward-looking ordered-basis
    # figure the editor steers; consumed_cost is the realised shipped-basis one that feeds
    # OrderRead.kitting_cogs. See kitting.get_order_kitting_summary for why both are here.
    unit_cost: Decimal
    unit_cost_is_frozen: bool
    effective_cost: Decimal
    consumed_cost: Decimal


class OrderKittingSummary(BaseModel):
    overrides: list[OrderKittingOverrideLine]
    lines: list[OrderKittingRequirementLine]
    effective_cost_total: Decimal
    consumed_cost_total: Decimal
