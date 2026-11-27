from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.work import WorkQtyBasis, WorkStageKind


class WorkSettingsRead(BaseModel):
    work_flow_enabled: bool
    work_flow_enabled_at: datetime | None = None


class WorkSettingsUpdate(BaseModel):
    work_flow_enabled: bool


class WorkStageBase(BaseModel):
    name: str
    # Free text; groups the Work page. NULL shows under "Other".
    station: str | None = None
    per_order: bool = False
    wait_minutes: int | None = Field(default=None, ge=0)
    typical_minutes: int | None = Field(default=None, ge=0)


class WorkStageCreate(WorkStageBase):
    pass


class WorkStageUpdate(BaseModel):
    """Every field optional: only keys actually sent are applied (see update_stage)."""

    name: str | None = None
    station: str | None = None
    per_order: bool | None = None
    wait_minutes: int | None = Field(default=None, ge=0)
    typical_minutes: int | None = Field(default=None, ge=0)


class WorkStageRead(WorkStageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: WorkStageKind
    created_at: datetime
    # How many routes include it, and how many progress events name it. Computed per request.
    route_count: int = 0
    event_count: int = 0


class WorkRouteStepInput(BaseModel):
    stage_id: int
    mix_variants: bool = False
    qty_basis: WorkQtyBasis = WorkQtyBasis.unit
    qty_category_id: int | None = None


class WorkRouteStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_id: int
    position: int
    mix_variants: bool
    qty_basis: WorkQtyBasis
    qty_category_id: int | None
    # Flattened from the stage so the route builder can render without a second lookup.
    stage_name: str
    stage_kind: WorkStageKind
    station: str | None
    per_order: bool


class WorkRouteCreate(BaseModel):
    name: str
    steps: list[WorkRouteStepInput]


class WorkRouteUpdate(BaseModel):
    name: str | None = None
    steps: list[WorkRouteStepInput] | None = None


class WorkRouteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool
    created_at: datetime
    steps: list[WorkRouteStepRead]
    product_count: int = 0
    category_count: int = 0


# ---- the Work list ----------------------------------------------------------------------------


class WorkVariantPart(BaseModel):
    """One variant's share of a (possibly mixed) batch card, for "3 x Design A, 3 x Design B"."""

    variant_id: int | None
    variant_name: str | None
    qty: int
    # What each of those stage units is for: how many finished items it represents.
    finished_units: int


class WorkCard(BaseModel):
    key: str
    stage_id: int
    stage_name: str
    stage_kind: WorkStageKind
    station: str | None
    product_id: int
    product_name: str
    parts: list[WorkVariantPart]
    # In the stage's own units (blanks for a count-by-category stage, finished items otherwise).
    qty: int
    unit_label: str | None = None
    due: datetime | None = None
    late: bool = False
    order_ids: list[int]
    # Set for a per-order stage (e.g. "make the file"): the one order line this card is for.
    order_line_id: int | None = None
    # Whether progress can be recorded here with the Done control. Make is recorded as a build, and
    # Ship happens on the order, so neither is.
    can_record: bool
    # Whether the Make card's finished items are ready to be built from prepared parts or from scratch.
    batch_note: str | None = None


class WorkShipLine(BaseModel):
    product_id: int
    product_name: str
    variant_name: str | None
    qty: int
    ready: bool


class WorkOrderCard(BaseModel):
    order_id: int
    platform: str | None
    buyer_name: str | None
    due: datetime | None = None
    late: bool = False
    lines: list[WorkShipLine]
    ready_lines: int
    total_lines: int
    ready: bool
    waiting_for: list[str]


class WorkStationRead(BaseModel):
    name: str | None
    count: int
    earliest_due: datetime | None = None


class WorkListRead(BaseModel):
    enabled: bool
    generated_at: datetime
    stations: list[WorkStationRead]
    # The station the Ship step belongs to, so a client filtering by station knows where "ready to
    # ship" and "waiting" sit.
    ship_station: str | None = None
    cards: list[WorkCard]
    ship_cards: list[WorkOrderCard]
    waiting_orders: list[WorkOrderCard]


class WorkSummaryRead(BaseModel):
    enabled: bool
    late: int
    due_today: int
    open_cards: int
    ready_to_ship: int


class WorkProgressCreate(BaseModel):
    product_id: int
    # NULL on a product with variants means "spread this across the variants the card covers".
    variant_id: int | None = None
    stage_id: int
    qty: int
    order_line_id: int | None = None
    note: str | None = None


class WorkEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    order_line_id: int | None
    stage_id: int
    qty: int
    note: str | None
    created_at: datetime
    # Filled in by the router for the history list.
    stage_name: str | None = None
    product_name: str | None = None
    variant_name: str | None = None
