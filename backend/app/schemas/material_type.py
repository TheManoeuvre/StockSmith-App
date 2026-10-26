from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MaterialTypeBase(BaseModel):
    name: str
    duplicates_in_kitting_check: bool = False


class MaterialTypeCreate(MaterialTypeBase):
    pass


class MaterialTypeFindOrCreate(BaseModel):
    name: str


class MaterialTypeUpdate(BaseModel):
    """Flag is `bool | None` so patch_row's exclude_unset leaves it alone when only renaming."""

    name: str
    duplicates_in_kitting_check: bool | None = None


class MaterialTypeRead(MaterialTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many rows reference this. Computed per request, not stored — see
    # routers/_reference_crud.py list_with_usage.
    usage_count: int = 0
