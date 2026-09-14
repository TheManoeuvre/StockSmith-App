from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.material import MaterialUnit


class MaterialCategory(Base):
    """A material category, promoted from a fixed seven-value enum to a real lookup row.

    Adding "vinyl" or "cardstock" used to mean editing a Python enum, a TypeScript union, two
    copied arrays, a label map and an Alembic migration to rebuild a CHECK constraint. Every
    other lookup in the app — manufacturer, supplier, material type, colour — had already made
    this move; category was the last one that hadn't.

    The reason it hadn't is that a category is not only a label. Four behaviours were keyed on a
    specific value, and they become columns here rather than `if name == "filament"` in five
    places:

    * `consumed_on_failed_build` — services/builds.py: a failed print still burns the filament,
      so those BOM lines default to consumed. Nothing else does.
    * `auto_kitting_per_order` — services/kitting.py: only packaging is auto-added as a
      per-order kitting override.
    * `tracks_colour` / `tracks_material_type` — the Materials pages only offered the Colour and
      Material type fields for filament. Two flags rather than one, because they are genuinely
      independent: resin plausibly has a colour and no material type, wooden blanks plausibly
      have a type (oak, walnut) and no hex.
    * `cost_per_kg_display` — filament is bought by the kilo and stocked by the gram, so its
      average cost reads ×1000. That is a display convention, not arithmetic.

    `default_unit` is a column rather than a flag because the behaviour it replaces
    (AUTO_EACH_CATEGORIES on the materials page) gave *every* category an implied default of
    `each` or `g`, not just the three named in the list. A boolean could not express "resin
    defaults to ml", which is the point of making this configurable. NULL means "leave whatever
    unit was picked alone", which is the right answer for a category the user just created and
    hasn't configured.

    `sort_order` exists because the original order — filament first, other last — was a
    deliberate frequency ordering, not alphabetical, and alphabetical would have been a visible
    regression on the materials list.
    """

    __tablename__ = "material_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Ascending. Ties are broken by name then id so the ordering is total even after a partial
    # reorder — nothing in the schema stops two rows sharing a value.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    default_unit: Mapped[MaterialUnit | None] = mapped_column(
        portable_enum(MaterialUnit, name="material_unit"), nullable=True
    )

    consumed_on_failed_build: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    auto_kitting_per_order: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    # Whether this category's materials are offered in the kitting-BOM material pickers (product
    # kitting BOM, variant kitting overrides, order kitting overrides). Seeded on for packaging
    # only — the rest of the catalogue is filament, resin and hardware that never gets packed
    # into a box, and hiding it keeps those pickers short. Purely a UI filter: nothing stops an
    # already-saved line pointing at a material whose category is unticked.
    show_in_kitting_bom_list: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    tracks_colour: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    tracks_material_type: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    cost_per_kg_display: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
