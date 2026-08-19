from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.abc_classification import ABCClass, ABCScope
from app.models.material import MaterialCategory


class TierInterval(BaseModel):
    """One tier's cadence, for one scope. `is_override` says whether this figure is stored
    or is the shipped default — the UI needs the distinction to offer a "reset" that means
    "delete the row" rather than "write the default back as an override"."""

    tier: ABCClass
    interval_days: int = Field(gt=0)
    is_override: bool = False


class CategoryTier(BaseModel):
    category: MaterialCategory
    abc_class: ABCClass


class ProductTypeTier(BaseModel):
    product_type_id: int
    abc_class: ABCClass


class StockCountSettingsRead(BaseModel):
    """The whole classification configuration in one payload.

    One endpoint rather than four: these are edited together on a single settings panel,
    and splitting them would make a coherent edit take four round trips and leave three
    ways to end up half-applied.
    """

    default_material_abc_class: ABCClass
    default_product_abc_class: ABCClass
    material_tier_intervals: list[TierInterval]
    product_tier_intervals: list[TierInterval]
    category_tiers: list[CategoryTier]
    product_type_tiers: list[ProductTypeTier]


class StockCountSettingsUpdate(BaseModel):
    """A full replacement of the configuration, not a patch.

    The tier/category/type lists are replace-in-full for the same reason the BOM editor is
    (PUT /products/{id}/bom): the UI edits them as one table, and "absent means delete"
    is the only reading that lets clearing a tier assignment work at all.
    """

    default_material_abc_class: ABCClass
    default_product_abc_class: ABCClass
    material_tier_intervals: list[TierInterval]
    product_tier_intervals: list[TierInterval]
    category_tiers: list[CategoryTier]
    product_type_tiers: list[ProductTypeTier]


class DueForCountItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope: ABCScope
    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    abc_class: ABCClass
    interval_days: int
    last_stock_take_at: datetime | None
    # None means never counted, which is a different thing from "0 days late" — see
    # services/abc.py due_state.
    days_overdue: int | None


class ResolvedClassificationRead(BaseModel):
    """An item's effective tier/cadence and where each came from, for its detail page."""

    model_config = ConfigDict(from_attributes=True)

    abc_class: ABCClass
    interval_days: int
    class_source: str
    interval_source: str
    last_stock_take_at: datetime | None
    next_due_at: datetime | None
    days_overdue: int | None
    is_due: bool
