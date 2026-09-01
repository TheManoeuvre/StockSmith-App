from datetime import datetime
from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.models.abc_classification import ABCClass
from app.models.material import MaterialAdjustmentMode, MaterialUnit
from app.schemas.abc import ResolvedClassificationRead


class MaterialBase(BaseModel):
    name: str
    # A category *name*, not the legacy enum, and accepted alongside category_id exactly as
    # colour/colour_id below: a client can send an id it picked from the list, or just a name
    # to be found-or-created. Keeping it a plain string on the wire is what lets the frontend
    # and the CSV importer carry on unchanged now that the set of valid names is open.
    category: str
    category_id: int | None = None
    unit: MaterialUnit
    reorder_threshold: Decimal = Decimal(0)
    # Both accepted, mirroring how material_type_name/material_type_id already behave: a client
    # can send a colour_id it picked, or just a name to be found-or-created. Nothing is forced
    # to change on day one, which is what keeps the CSV importer and the existing UI working.
    colour: str | None = None
    colour_id: int | None = None
    material_type_id: int | None = None
    barcode: str | None = None
    manufacturer_id: int | None = None
    default_supplier_id: int | None = None
    typical_reorder_qty: Decimal | None = None
    product_url: str | None = None
    # NULL means "inherit" for both — see services/abc.py. Deliberately settable on
    # create/update while last_stock_take_at is not: the count date is written only by an
    # approved stock take, never by editing the material.
    abc_class: ABCClass | None = None
    stock_take_interval_days: int | None = Field(default=None, gt=0)

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
    category: str | None = None
    category_id: int | None = None
    unit: MaterialUnit | None = None
    reorder_threshold: Decimal | None = None
    is_active: bool | None = None
    colour: str | None = None
    colour_id: int | None = None
    material_type_id: int | None = None
    barcode: str | None = None
    manufacturer_id: int | None = None
    default_supplier_id: int | None = None
    typical_reorder_qty: Decimal | None = None
    product_url: str | None = None
    abc_class: ABCClass | None = None
    stock_take_interval_days: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class MaterialRead(MaterialBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    # Reads through the relationship, falling back to the legacy column — see
    # Material.category_name. Aliased rather than renamed so every existing consumer of
    # `category` (the CSV export, the materials list, the test fixtures) is unaffected.
    category: str | None = Field(default=None, validation_alias=AliasChoices("category_name", "category"))
    current_qty: Decimal
    allocated_qty: Decimal
    avg_unit_cost: Decimal
    is_active: bool
    # Reads through the relationship, falling back to the legacy column — see
    # Material.colour_name. Aliased rather than renamed so every existing consumer of `colour`
    # (the CSV export, the materials list, materialDetail.test.tsx) is unaffected.
    colour: str | None = Field(default=None, validation_alias=AliasChoices("colour_name", "colour"))
    # The reference colour's hex code when it has one, for the list swatch. Null for materials
    # still on the legacy free-text colour path — see Material.colour_hex.
    colour_hex: str | None = None
    manufacturer_name: str | None = None
    default_supplier_name: str | None = None
    material_type_name: str | None = None
    image_path: str | None = None
    image_original_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    on_order_qty: Decimal | None = None
    # Time-to-stockout forecast, injected from services/forecasting.py on the list and
    # single-get paths (null on a mutation response — the client refetches). `weeks_of_supply`
    # is null when there's too little sales history; `stockout_status` is then
    # "insufficient_data". "ok" means healthy.
    weeks_of_supply: Decimal | None = None
    consumption_rate_per_week: Decimal | None = None
    fg_buffer_weeks: Decimal | None = None
    stockout_status: str | None = None
    # Products whose build/kitting BOM names this material — populated on the single-get only
    # (the list leaves it null), for the detail panel's "Used in N products" footer.
    used_in_product_count: int | None = None
    last_stock_take_at: datetime | None = None
    # The effective tier/cadence with its provenance, so the UI can say "C, from the
    # Packaging category" rather than a bare "C" that gives no clue which level to edit.
    # Populated on the list and single-get paths; a mutation response leaves it null and
    # the client refetches, which is what every mutation here already does.
    classification: ResolvedClassificationRead | None = None


class DraftPurchaseCreate(BaseModel):
    qty: Decimal | None = None


class MaterialAdjustmentCreate(BaseModel):
    mode: MaterialAdjustmentMode = MaterialAdjustmentMode.adjust
    value: Decimal
    reason: str
