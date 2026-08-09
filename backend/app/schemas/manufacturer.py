from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ManufacturerBase(BaseModel):
    name: str
    website_url: str | None = None


class ManufacturerCreate(ManufacturerBase):
    pass


class ManufacturerFindOrCreate(BaseModel):
    name: str


class ManufacturerUpdate(BaseModel):
    name: str
    website_url: str | None = None


class ManufacturerRead(ManufacturerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
