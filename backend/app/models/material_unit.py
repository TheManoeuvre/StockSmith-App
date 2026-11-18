from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MaterialUnitRow(Base):
    """A unit of measure for materials, promoted from a fixed three-value enum to a lookup row.

    Adding metres used to mean editing a Python enum, a TypeScript union, two copied arrays, the
    display helpers and a migration. The three behaviours that were keyed on a specific unit
    become columns here instead:

    * `decimal_places` — how fine a quantity in this unit may be. `0` is what "each" always was
      (you cannot have half a screw); `2` suits metres; NULL is what grams and millilitres were,
      any precision at all, stored to four places and shown trimmed. A bounded unit is also
      *displayed* to exactly that many places, so 1.5 m reads "1.50 m".
    * `show_suffix` — "each" never printed its name next to a number ("12", not "12 each"), because
      spelling it out on every row of a coaster order is clutter. A flag rather than a special
      case, so any countable unit can opt out the same way.

    `materials.unit` and `material_categories.default_unit` hold this row's `name`, not an id,
    and are not foreign keys. That is a deliberate trade: every one of the ~60 places that builds
    a `Material(unit=...)` keeps working, the API keeps sending `"g"` rather than an object, and
    the cost — renaming a unit has to rewrite the strings that reference it, and deleting one has
    to check nobody uses it — lives in one place, services/units.py.
    """

    __tablename__ = "material_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The symbol as shown beside a number: "g", "ml", "m". Matched case-insensitively on create,
    # because "M" and "m" are not two units anyone meant.
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # NULL = any precision. 0 = whole numbers only. Existing stock is never rewritten when this
    # changes; it only governs quantities entered from then on.
    decimal_places: Mapped[int | None] = mapped_column(Integer, nullable=True)
    show_suffix: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    # Ascending. Decides the order of the unit pickers; ties broken by name then id.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
