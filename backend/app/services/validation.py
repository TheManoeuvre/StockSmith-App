from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material, MaterialUnit


def validate_qty_for_unit(qty: Decimal, unit: MaterialUnit, field_name: str = "qty") -> None:
    """Materials measured in 'each' can't have fractional quantities — you can't have
    half a screw. g/ml stay precise Decimal (rounded only for display)."""
    if unit == MaterialUnit.each and qty != qty.to_integral_value():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a whole number for materials measured in 'each'",
        )


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
    for material_id, qty in lines:
        unit = unit_by_id.get(material_id)
        if unit is not None:
            validate_qty_for_unit(qty, unit, field_name)
