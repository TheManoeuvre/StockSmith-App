from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ManufacturerBase(BaseModel):
    name: str
    website_url: str | None = None


class ManufacturerCreate(ManufacturerBase):
    pass


class ManufacturerFindOrCreate(BaseModel):
    name: str


class ManufacturerRead(ManufacturerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
