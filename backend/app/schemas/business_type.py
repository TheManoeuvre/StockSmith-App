from pydantic import BaseModel


class BusinessPresetRead(BaseModel):
    key: str
    label: str
    description: str
    # Previews of what choosing this preset sets up, for the picker to show before committing.
    categories: list[str]
    units: list[str]
    material_types: list[str]


class BusinessTypeRead(BaseModel):
    # NULL until chosen — the frontend shows the first-launch picker on exactly that.
    business_type: str | None
    # Whether choosing is still possible (no materials yet). False once the catalogue exists.
    can_choose: bool
    presets: list[BusinessPresetRead]


class BusinessTypeUpdate(BaseModel):
    business_type: str
