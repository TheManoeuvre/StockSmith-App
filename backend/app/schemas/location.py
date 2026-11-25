from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.location import PlacementEventKind


class ItemRefIn(BaseModel):
    """Exactly one of: a material, or a product (optionally with a variant)."""

    material_id: int | None = None
    product_id: int | None = None
    variant_id: int | None = None


class LocationCreate(BaseModel):
    name: str = Field(min_length=1)
    parent_id: int | None = None
    notes: str | None = None


class LocationUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None
    # Present-and-null means "move to top level", so the router checks model_fields_set.
    parent_id: int | None = None


class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str
    code: str
    notes: str | None
    archived: bool
    own_items: int
    rolled_up_items: int
    needs_attention: bool


class LocationTreeRead(BaseModel):
    locations: list[LocationRead]
    total_locations: int
    attention_locations: int
    empty_locations: int
    unplaced_items: int
    placeable_items: int


class RangeRequest(BaseModel):
    parent_id: int | None = None
    pattern: str


class RangePreviewName(BaseModel):
    name: str
    exists: bool


class RangePreviewRead(BaseModel):
    names: list[RangePreviewName]
    new_count: int
    error: str | None = None


class PathSegment(BaseModel):
    id: int
    name: str


class ContentRowRead(BaseModel):
    placement_id: int
    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    sku: str | None
    unit: str
    qty: Decimal
    is_primary: bool
    drift: Decimal


class HistoryRead(BaseModel):
    id: int
    kind: PlacementEventKind
    item_name: str
    qty: Decimal
    from_path: str | None
    to_path: str | None
    reason: str | None
    created_at: datetime


class LocationDetailRead(BaseModel):
    id: int
    parent_id: int | None
    name: str
    code: str
    notes: str | None
    archived: bool
    path: list[PathSegment]
    contents: list[ContentRowRead]
    children: list[LocationRead]
    history: list[HistoryRead]
    history_count: int


class PlaceRequest(ItemRefIn):
    location_id: int
    qty: Decimal


class MoveItemRequest(ItemRefIn):
    from_location_id: int
    to_location_id: int
    qty: Decimal


class EmptyIntoRequest(BaseModel):
    to_location_id: int


class EmptyIntoResult(BaseModel):
    moved: int


class QtyRequest(BaseModel):
    qty: Decimal


class PlacementRead(BaseModel):
    id: int
    location_id: int
    location_path: list[str]
    qty: Decimal
    is_primary: bool


class StoredInRead(BaseModel):
    name: str
    unit: str
    total: Decimal
    placed: Decimal
    unassigned: Decimal
    drift: Decimal
    follows_stock: bool
    placements: list[PlacementRead]


class ItemSummaryRead(BaseModel):
    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    sku: str | None
    unit: str
    primary_path: list[str] | None
    extra_places: int
    placed: Decimal
    total: Decimal
    drift: Decimal
