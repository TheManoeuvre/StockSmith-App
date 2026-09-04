from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SupplierBase(BaseModel):
    name: str
    website_url: str | None = None
    # Business days (Mon-Fri). None means "fall back to the shop-wide default lead time" —
    # see the model.
    default_lead_time_days: int | None = None


class SupplierCreate(SupplierBase):
    pass


class SupplierFindOrCreate(BaseModel):
    name: str


class SupplierUpdate(BaseModel):
    name: str
    website_url: str | None = None
    default_lead_time_days: int | None = None


class SupplierRead(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
