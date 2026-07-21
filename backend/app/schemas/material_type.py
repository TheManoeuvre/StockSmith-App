from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MaterialTypeBase(BaseModel):
    name: str


class MaterialTypeCreate(MaterialTypeBase):
    pass


class MaterialTypeFindOrCreate(BaseModel):
    name: str


class MaterialTypeRead(MaterialTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
