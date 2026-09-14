from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MaterialSubstituteCreate(BaseModel):
    substitute_material_id: int
    rank: int = 0
    notes: str | None = None
    created_by: str | None = None


class MaterialSubstituteUpdate(BaseModel):
    """Covers both the "reorder" and "deactivate" halves of the CRUD surface — a client
    reorders by PATCHing each row's rank, and deactivates by PATCHing is_active=False,
    rather than this needing two different endpoints."""

    rank: int | None = None
    notes: str | None = None
    is_active: bool | None = None


class MaterialSubstituteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material_id: int
    substitute_material_id: int
    substitute_material_name: str | None = None
    rank: int
    notes: str | None = None
    created_by: str | None = None
    is_active: bool
    created_at: datetime


class SubstituteSuggestion(BaseModel):
    """A ranked, human-curated fallback surfaced alongside a detected shortage —
    suggested only, never auto-applied. `available_qty` is the substitute's own
    current_qty minus allocated_qty (floored at 0), so whoever is picking a fallback can
    see at a glance whether it would actually cover the gap."""

    material_id: int
    material_name: str
    rank: int
    notes: str | None = None
    available_qty: Decimal


class MaterialSubstituteUsageCreate(BaseModel):
    material_id: int
    substitute_material_id: int
    qty: Decimal | None = None
    order_id: int | None = None
    build_id: int | None = None
    notes: str | None = None
    created_by: str | None = None


class MaterialSubstituteUsageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material_id: int
    substitute_material_id: int
    qty: Decimal | None = None
    order_id: int | None = None
    build_id: int | None = None
    notes: str | None = None
    created_by: str | None = None
    created_at: datetime
