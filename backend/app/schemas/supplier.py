from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SupplierBase(BaseModel):
    name: str
    website_url: str | None = None


class SupplierCreate(SupplierBase):
    pass


class SupplierFindOrCreate(BaseModel):
    name: str


class SupplierRead(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
