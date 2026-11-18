from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.services import units


async def validate_qty_for_unit(
    session: AsyncSession, qty: Decimal, unit: str, field_name: str = "qty"
) -> None:
    """Refuse a quantity finer than the material's unit allows — 'each' takes whole numbers only
    (you can't have half a screw), metres might take two decimal places, grams anything. The rule
    is the unit's `decimal_places`, edited under Settings → Lists.

    A unit name with no row (a database written before the units table, or by hand) is not
    refused: there is no rule to enforce, and blocking stock entry over a missing lookup row would
    be worse than the loose input.
    """
    row = await units.get_unit(session, unit)
    if row is not None:
        units.check_qty(qty, row, field_name)


async def validate_lines_against_units(
    session: AsyncSession, lines: list[tuple[int, Decimal]], field_name: str = "qty"
) -> None:
    """Batch-fetches the referenced materials' units and validates each (material_id, qty)
    pair. Used for BOM lines, BOM overrides, and purchase lines — anywhere a material_id +
    qty pair is written but the row itself has no unit column of its own to self-validate."""
    if not lines:
        return
    material_ids = {material_id for material_id, _ in lines}
    result = await session.execute(select(Material.id, Material.unit).where(Material.id.in_(material_ids)))
    unit_by_id = {row.id: row.unit for row in result}
    rows = {
        unit.name: unit
        for unit in (await session.execute(select(units.MaterialUnitRow))).scalars()
    }
    for material_id, qty in lines:
        unit = rows.get(unit_by_id.get(material_id))
        if unit is not None:
            units.check_qty(qty, unit, field_name)
