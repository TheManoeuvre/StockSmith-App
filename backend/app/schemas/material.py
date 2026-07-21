from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.material import MaterialAdjustmentMode, MaterialCategory, MaterialUnit


class MaterialBase(BaseModel):
    name: str
    category: MaterialCategory
    unit: MaterialUnit
    reorder_threshold: Decimal = Decimal(0)
    colour: str | None = None
    material_type_id: int | None = None
    barcode: str | None = None
    manufacturer_id: int | None = None
    default_supplier_id: int | None = None
    typical_reorder_qty: Decimal | None = None
    product_url: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        # Stray leading/trailing whitespace here breaks exact-name matching elsewhere
        # (e.g. CSV import's update-vs-create lookup) in a way that's easy to introduce
        # by accident and hard to notice afterward.
        return value.strip()


class MaterialCreate(MaterialBase):
    pass


class MaterialUpdate(BaseModel):
    name: str | None = None
    category: MaterialCategory | None = None
    unit: MaterialUnit | None = None
    reorder_threshold: Decimal | None = None
    is_active: bool | None = None
    colour: str | None = None
    material_type_id: int | None = None
    barcode: str | None = None
    manufacturer_id: int | None = None
    default_supplier_id: int | None = None
    typical_reorder_qty: Decimal | None = None
    product_url: str | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class MaterialRead(MaterialBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    current_qty: Decimal
    allocated_qty: Decimal
    avg_unit_cost: Decimal
    is_active: bool
    manufacturer_name: str | None = None
    default_supplier_name: str | None = None
    material_type_name: str | None = None
    image_path: str | None = None
    image_original_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    on_order_qty: Decimal | None = None


class DraftPurchaseCreate(BaseModel):
    qty: Decimal | None = None


class MaterialAdjustmentCreate(BaseModel):
    mode: MaterialAdjustmentMode = MaterialAdjustmentMode.adjust
    value: Decimal
    reason: str
