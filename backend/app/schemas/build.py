from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BuildCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    qty_built: int = 0
    qty_failed: int = 0
    # material_id -> whether that BOM line was consumed for the failed qty. Optional —
    # when omitted and qty_failed > 0, the service defaults to "filament consumed,
    # everything else not".
    failed_consumption: dict[int, bool] | None = None
    notes: str | None = None


class BuildRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    qty_built: int
    qty_failed: int
    notes: str | None
    built_at: datetime
