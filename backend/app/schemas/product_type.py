from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProductTypeBase(BaseModel):
    name: str


class ProductTypeCreate(ProductTypeBase):
    pass


class ProductTypeFindOrCreate(BaseModel):
    name: str


class ProductTypeUpdate(BaseModel):
    name: str


class ProductTypeRead(ProductTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
