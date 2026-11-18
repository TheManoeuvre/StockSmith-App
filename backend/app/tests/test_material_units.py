"""Units of measure as editable rows: the API, the per-unit quantity rule, and rename/delete."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.material_unit import MaterialUnitRow
from app.routers import material_units
from app.schemas.material_unit import MaterialUnitFindOrCreate, MaterialUnitUpdate
from app.services import units
from app.services.validation import validate_lines_against_units, validate_qty_for_unit


async def _metres(session, decimal_places=2) -> MaterialUnitRow:
    row = await material_units.find_or_create_material_unit(MaterialUnitFindOrCreate(name="m"), session)
    row.decimal_places = decimal_places
    await session.commit()
    return row


async def _material(session, unit: str, name="Ribbon") -> Material:
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    material = Material(name=name, category="other", category_id=category.id, unit=unit)
    session.add(material)
    await session.commit()
    return material


async def test_the_three_builtin_units_are_seeded_with_their_original_behaviour(session):
    rows = {row.name: row for row in await material_units.list_material_units(session)}
    assert set(rows) == {"g", "ml", "each"}
    assert rows["each"].decimal_places == 0 and rows["each"].show_suffix is False
    assert rows["g"].decimal_places is None and rows["g"].show_suffix is True


async def test_find_or_create_is_case_insensitive(session):
    first = await material_units.find_or_create_material_unit(MaterialUnitFindOrCreate(name="m"), session)
    again = await material_units.find_or_create_material_unit(MaterialUnitFindOrCreate(name=" M "), session)
    assert again.id == first.id


async def test_a_two_decimal_unit_accepts_two_places_and_refuses_three(session):
    await _metres(session, decimal_places=2)
    await validate_qty_for_unit(session, Decimal("1.25"), "m", "qty")
    with pytest.raises(HTTPException) as exc:
        await validate_qty_for_unit(session, Decimal("1.255"), "m", "qty")
    assert exc.value.status_code == 400
    assert "2 decimal places" in exc.value.detail


async def test_each_still_refuses_fractions_and_grams_still_accept_any(session):
    with pytest.raises(HTTPException) as exc:
        await validate_qty_for_unit(session, Decimal("2.5"), "each", "qty")
    assert "whole number" in exc.value.detail
    await validate_qty_for_unit(session, Decimal("2.5001"), "g", "qty")


async def test_the_rule_follows_an_edit_to_the_unit(session):
    row = await _metres(session, decimal_places=2)
    await material_units.update_material_unit(
        row.id, MaterialUnitUpdate(name="m", decimal_places=0), session
    )
    with pytest.raises(HTTPException):
        await validate_qty_for_unit(session, Decimal("0.5"), "m", "qty")


async def test_lines_are_validated_against_their_materials_unit(session):
    await _metres(session, decimal_places=1)
    ribbon = await _material(session, "m")
    with pytest.raises(HTTPException):
        await validate_lines_against_units(session, [(ribbon.id, Decimal("1.25"))])
    await validate_lines_against_units(session, [(ribbon.id, Decimal("1.2"))])


async def test_renaming_a_unit_carries_its_materials_and_category_defaults(session):
    row = await _metres(session)
    ribbon = await _material(session, "m")
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    category.default_unit = "m"
    await session.commit()

    await material_units.update_material_unit(row.id, MaterialUnitUpdate(name="metre"), session)

    await session.refresh(ribbon)
    await session.refresh(category)
    assert ribbon.unit == "metre"
    assert category.default_unit == "metre"


async def test_renaming_onto_another_unit_is_a_conflict(session):
    row = await _metres(session)
    with pytest.raises(HTTPException) as exc:
        await material_units.update_material_unit(row.id, MaterialUnitUpdate(name="G"), session)
    assert exc.value.status_code == 409


async def test_a_unit_in_use_cannot_be_deleted_but_an_unused_one_can(session):
    row = await _metres(session)
    await _material(session, "m")
    with pytest.raises(HTTPException) as exc:
        await material_units.delete_material_unit(row.id, session)
    assert exc.value.status_code == 409

    spare = await material_units.find_or_create_material_unit(MaterialUnitFindOrCreate(name="kg"), session)
    await material_units.delete_material_unit(spare.id, session)
    assert await units.get_unit(session, "kg") is None


async def test_usage_count_includes_category_defaults(session):
    row = await _metres(session)
    await _material(session, "m")
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    category.default_unit = "m"
    await session.commit()

    listed = {r.name: r for r in await material_units.list_material_units(session)}
    assert listed["m"].usage_count == 2
    assert row.name == "m"


async def test_creating_a_material_in_an_unknown_unit_is_refused(session):
    with pytest.raises(HTTPException) as exc:
        await units.require_unit(session, "furlong")
    assert exc.value.status_code == 400
    assert "Settings" in exc.value.detail
