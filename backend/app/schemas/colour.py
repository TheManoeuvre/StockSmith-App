from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ColourBase(BaseModel):
    name: str
    hex_code: str | None = None


class ColourCreate(ColourBase):
    pass


class ColourFindOrCreate(BaseModel):
    name: str


class ColourUpdate(BaseModel):
    name: str
    hex_code: str | None = None


class ColourRead(ColourBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many materials reference this. Computed per request.
    usage_count: int = 0
