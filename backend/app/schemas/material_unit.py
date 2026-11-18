from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.units import MAX_DECIMAL_PLACES


class MaterialUnitFindOrCreate(BaseModel):
    name: str


class MaterialUnitUpdate(BaseModel):
    """Optional and nullable throughout, because the router dumps this with exclude_unset=True: a
    field that isn't sent must stay unset so it isn't written. `decimal_places` is the one where
    null is a real value — "any precision"."""

    name: str
    decimal_places: int | None = Field(default=None, ge=0, le=MAX_DECIMAL_PLACES)
    show_suffix: bool | None = None
    sort_order: int | None = None


class MaterialUnitReorder(BaseModel):
    ids: list[int]


class MaterialUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    # NULL = any precision (shown trimmed); 0 = whole numbers; N = at most N places, shown to N.
    decimal_places: int | None = None
    show_suffix: bool = True
    sort_order: int = 0
    created_at: datetime
    # Materials and category defaults using this unit. Computed per request.
    usage_count: int = 0
