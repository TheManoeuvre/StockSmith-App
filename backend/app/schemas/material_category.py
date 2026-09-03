from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.material import MaterialUnit


class MaterialCategoryBase(BaseModel):
    name: str
    sort_order: int = 0
    # NULL means "don't change the unit the user picked". Only a category that has been given
    # one imposes it.
    default_unit: MaterialUnit | None = None
    consumed_on_failed_build: bool = False
    auto_kitting_per_order: bool = False
    show_in_kitting_bom_list: bool = False
    tracks_colour: bool = False
    tracks_material_type: bool = False
    cost_per_kg_display: bool = False


class MaterialCategoryCreate(MaterialCategoryBase):
    pass


class MaterialCategoryFindOrCreate(BaseModel):
    name: str


class MaterialCategoryUpdate(BaseModel):
    """Every field but the name is optional *and* nullable.

    `bool | None` rather than a plain `bool` with a default, because patch_row dumps this with
    exclude_unset=True: a field that isn't sent must stay unset so it isn't written at all. Give
    the flags a default of False instead and a request naming only the name would silently clear
    every flag on the row.
    """

    name: str
    sort_order: int | None = None
    default_unit: MaterialUnit | None = None
    consumed_on_failed_build: bool | None = None
    auto_kitting_per_order: bool | None = None
    show_in_kitting_bom_list: bool | None = None
    tracks_colour: bool | None = None
    tracks_material_type: bool | None = None
    cost_per_kg_display: bool | None = None


class MaterialCategoryReorder(BaseModel):
    """Ids in their new order.

    One request rather than a PATCH per row: a reorder is a single intent, and N sequential
    PATCHes would leave transient duplicate positions behind if one of them failed.
    """

    ids: list[int]


class MaterialCategoryRead(MaterialCategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    # How many materials reference this. Computed per request.
    usage_count: int = 0
