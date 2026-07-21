from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.asset import AssetType


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    asset_type: AssetType
    file_path: str
    original_filename: str
    display_order: int
    created_at: datetime


class AssetUpdate(BaseModel):
    display_order: int | None = None
