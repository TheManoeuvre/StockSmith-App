from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MaterialTypeBase(BaseModel):
    name: str


class MaterialTypeCreate(MaterialTypeBase):
    pass


class MaterialTypeFindOrCreate(BaseModel):
    name: str


class MaterialTypeUpdate(BaseModel):
    name: str


class MaterialTypeRead(MaterialTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
