"""Units of measure: lookup, the quantity rule, and keeping the referencing strings in step.

`materials.unit` and `material_categories.default_unit` hold a unit's *name*, not an id (see
models/material_unit.py for why), so the two things a foreign key would have done for free live
here: a rename rewrites those strings, and a delete first checks nobody uses the unit.
"""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.material_unit import MaterialUnitRow

# The three units the app shipped with, in the order they were offered. The migration that
# creates the table inlines its own copy rather than importing this one — a migration records what
# happened at one revision, and one that imports app code silently re-runs against whatever that
# code says later.
DEFAULT_UNITS: tuple[dict, ...] = (
    {"name": MaterialUnit.g.value, "sort_order": 10, "decimal_places": None, "show_suffix": True},
    {"name": MaterialUnit.ml.value, "sort_order": 20, "decimal_places": None, "show_suffix": True},
    {"name": MaterialUnit.each.value, "sort_order": 30, "decimal_places": 0, "show_suffix": False},
)

# Stored quantities are Numeric(14, 4), so a rule finer than this could never be satisfied by what
# the database keeps.
MAX_DECIMAL_PLACES = 4


async def get_unit(session: AsyncSession, name: str | None) -> MaterialUnitRow | None:
    """Exact match on the stored name — what `materials.unit` holds is always a name taken from
    this table, so there is nothing to fold."""
    if name is None:
        return None
    # `.value` for a MaterialUnit member: str() of a str-mixin enum is "MaterialUnit.g", not "g",
    # and a just-created Material still holds whatever its constructor was given.
    name = getattr(name, "value", name)
    return (
        await session.execute(select(MaterialUnitRow).where(MaterialUnitRow.name == name))
    ).scalar_one_or_none()


async def find_by_name_ci(session: AsyncSession, name: str) -> MaterialUnitRow | None:
    return (
        await session.execute(
            select(MaterialUnitRow).where(func.lower(MaterialUnitRow.name) == name.strip().lower())
        )
    ).scalar_one_or_none()


async def require_unit(session: AsyncSession, name: str | None) -> MaterialUnitRow:
    """The unit row for `name`, or a 400 saying which units exist.

    Without this a typo'd unit would be stored as-is, since nothing in the schema stops it, and
    then silently behave as "any precision, show the name".
    """
    unit = await get_unit(session, name)
    if unit is None:
        known = (await session.execute(select(MaterialUnitRow.name).order_by(MaterialUnitRow.sort_order))).scalars()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown unit '{name}'. Units are managed in Settings → Lists; known units: "
            f"{', '.join(known)}",
        )
    return unit


def check_qty(qty: Decimal, unit: MaterialUnitRow, field_name: str = "qty") -> None:
    """Refuse a quantity finer than the unit allows. 400, because the request is well-formed —
    this value just can't be expressed in this unit."""
    places = unit.decimal_places
    if places is None:
        return
    if qty != qty.quantize(Decimal(1).scaleb(-places)):
        if places == 0:
            detail = f"{field_name} must be a whole number for materials measured in '{unit.name}'"
        else:
            noun = "decimal place" if places == 1 else "decimal places"
            detail = f"{field_name} can have at most {places} {noun} for materials measured in '{unit.name}'"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def next_sort_order(session: AsyncSession) -> int:
    current = (await session.execute(select(func.max(MaterialUnitRow.sort_order)))).scalar()
    return (current or 0) + 10


async def usage_counts(session: AsyncSession) -> dict[str, int]:
    """How many materials and categories use each unit, keyed by unit name."""
    totals: dict[str, int] = {}
    for column in (Material.unit, MaterialCategory.default_unit):
        result = await session.execute(select(column, func.count()).where(column.is_not(None)).group_by(column))
        for name, count in result:
            totals[str(name)] = totals.get(str(name), 0) + count
    return totals


async def rename_references(session: AsyncSession, old: str, new: str) -> None:
    """Point every material and category default at a unit's new name. Does not commit."""
    await session.execute(update(Material).where(Material.unit == old).values(unit=new))
    await session.execute(
        update(MaterialCategory).where(MaterialCategory.default_unit == old).values(default_unit=new)
    )
