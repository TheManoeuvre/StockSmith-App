from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProductCategoryBase(BaseModel):
    name: str


class ProductCategoryCreate(ProductCategoryBase):
    pass


class ProductCategoryFindOrCreate(BaseModel):
    name: str


class ProductCategoryUpdate(BaseModel):
    name: str


class ProductCategoryRead(ProductCategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
