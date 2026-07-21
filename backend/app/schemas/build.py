from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BuildCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    qty_built: int
    notes: str | None = None


class BuildRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    qty_built: int
    notes: str | None
    built_at: datetime
