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
