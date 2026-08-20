"""Stock take CSV export and the two-call import (services/csv_io.py).

The import is called twice with the same file — once to preview, once to apply — so the
tests that matter most are the ones proving the first call writes nothing and that a
refused file leaves the sheet exactly as it was. Half-applying a count sheet is worse than
rejecting it, because the half that landed looks indistinguishable from a real count.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialAdjustment, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.product import Product
from app.models.stock_take import StockTakeLine, StockTakeLineStatus
from app.schemas.stock_take import StockTakeScope
from app.services import csv_io, stock_takes
from app.services.material_categories import legacy_value_for
from app.services.csv_io import export_stock_take_csv, import_stock_take_csv

EVERYTHING = StockTakeScope(include_materials=True, include_products=True)




async def _category(session, name: str) -> MaterialCategory:
    """The seeded category row of that name — conftest puts the original seven in place."""
    return (
        await session.execute(select(MaterialCategory).where(MaterialCategory.name == name))
    ).scalar_one()


async def _fixture_take(session):
    session.add(GeneralSettings(id=1))
    material = Material(
        name="Grey Resin",
        category=legacy_value_for("resin"),
        category_id=(await _category(session, "resin")).id,
        unit=MaterialUnit.ml,
        current_qty=Decimal(10),
    )
    session.add(material)
    await session.flush()
    session.add(MaterialAdjustment(material_id=material.id, mode="adjust", qty_delta=Decimal(10), reason="in"))
    session.add(Product(name="Oak Coaster", sku="OAK-1", current_stock=4))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, EVERYTHING)
    return take


async def _lines(session, take_id):
    """The take's lines in the order the sheet shows them.

    Not creation order. The two used to be the same thing, and the tests below index rows
    and lines positionally against each other on that basis — so this has to follow the
    sheet, or every one of them quietly compares the wrong pair.
    """
    lines = list(
        (
            await session.execute(
                select(StockTakeLine).where(StockTakeLine.stock_take_id == take_id).order_by(StockTakeLine.id)
            )
        ).scalars()
    )
    materials, products, variants = await csv_io._stock_take_lookups(session, lines)
    return [g.line for g in stock_takes.group_lines(lines, materials, products, variants)]


def _rows(csv_text: str) -> list[dict]:
    import csv as _csv
    import io as _io

    return list(_csv.DictReader(_io.StringIO(csv_text)))


def _sheet(rows: list[dict]) -> bytes:
    import csv as _csv
    import io as _io

    from app.services.csv_io import STOCK_TAKE_CSV_FIELDS

    buf = _io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=STOCK_TAKE_CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in STOCK_TAKE_CSV_FIELDS})
    return buf.getvalue().encode()


# --- export ----------------------------------------------------------------------------


async def test_export_carries_what_the_counter_needs(session):
    take = await _fixture_take(session)

    rows = _rows(await export_stock_take_csv(session, take.id))

    # Products first, then materials, each under the headings the screen shows — the sheet
    # is the copy that gets printed and walked around with, so it is the one that most has
    # to match the shelves.
    assert [r["item_type"] for r in rows] == ["product", "material"]
    assert [r["name"] for r in rows] == ["Oak Coaster", "Grey Resin"]
    assert [r["section"] for r in rows] == ["Products", "Materials"]
    assert [r["subgroup"] for r in rows] == ["OAK-1", ""]
    assert rows[1]["group"] == "resin"
    assert [r["unit"] for r in rows] == ["each", "ml"]
    assert [Decimal(r["expected_qty"]) for r in rows] == [Decimal(4), Decimal(10)]
    # Blank on a fresh sheet — that's what the counter fills in.
    assert [r["counted_qty"] for r in rows] == ["", ""]


async def test_export_shows_allocated_stock_on_the_sheet(session):
    """The printed sheet is exactly where nobody would otherwise know some units are boxed
    rather than on the shelf."""
    session.add(GeneralSettings(id=1))
    session.add(Product(name="Boxed", sku="B-1", current_stock=12, allocated_qty=5))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, StockTakeScope(include_products=True))

    row = _rows(await export_stock_take_csv(session, take.id))[0]

    assert Decimal(row["expected_qty"]) == Decimal(12)
    assert Decimal(row["allocated_qty"]) == Decimal(5)


async def test_export_round_trips_counting_already_under_way(session):
    take = await _fixture_take(session)
    line = (await _lines(session, take.id))[0]
    await stock_takes.set_line_count(session, take.id, line.id, Decimal(8), "half a bottle")

    row = next(r for r in _rows(await export_stock_take_csv(session, take.id)) if r["line_id"] == str(line.id))

    assert Decimal(row["counted_qty"]) == Decimal(8)
    assert row["notes"] == "half a bottle"


# --- import ----------------------------------------------------------------------------


async def test_round_trip_applies_the_counts(session):
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[1]["counted_qty"] = "3"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False)

    assert (result["matched"], result["applied"], result["failed"]) == (2, True, [])
    assert [Decimal(line.counted_qty) for line in await _lines(session, take.id)] == [Decimal(8), Decimal(3)]
    assert {line.status for line in await _lines(session, take.id)} == {StockTakeLineStatus.counted}


async def test_a_dry_run_writes_nothing(session):
    """The preview must be a preview — this is what the confirmation screen is built on."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=True)

    assert (result["matched"], result["applied"]) == (1, False)
    assert all(line.counted_qty is None for line in await _lines(session, take.id))


async def test_a_blank_count_leaves_its_line_untouched(session):
    """Blank is "didn't get to this one", not zero — same rule as leaving the field empty
    in the app."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[1]["counted_qty"] = ""

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False)

    assert result["skipped_blank"] == 1
    lines = await _lines(session, take.id)
    assert Decimal(lines[0].counted_qty) == Decimal(8)
    assert lines[1].counted_qty is None
    assert lines[1].status is StockTakeLineStatus.pending


async def test_zero_is_a_real_count(session):
    """The counterpart to the test above: 0 means "there are none", and must be applied."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "0"

    await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False)

    line = (await _lines(session, take.id))[0]
    assert Decimal(line.counted_qty) == Decimal(0)
    assert line.status is StockTakeLineStatus.counted


async def test_skip_applies_the_good_rows_and_reports_the_rest(session):
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[1]["counted_qty"] = "not a number"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert result["applied"] is True
    assert [f["row"] for f in result["failed"]] == [3]  # header is row 1
    # Readable, because this is what the confirmation screen shows someone who has to go
    # and fix the file — not Decimal's own "[<class 'decimal.ConversionSyntax'>]".
    assert result["failed"][0]["error"] == "'not a number' is not a number"
    lines = await _lines(session, take.id)
    assert Decimal(lines[0].counted_qty) == Decimal(8)
    assert lines[1].counted_qty is None


async def test_fail_writes_nothing_when_any_row_is_bad(session):
    """All-or-nothing means nothing — a half-applied sheet is worse than a rejected one,
    because the half that landed looks like a real count."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[1]["counted_qty"] = "not a number"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="fail")

    assert result["applied"] is False
    assert all(line.counted_qty is None for line in await _lines(session, take.id))


async def test_excel_byte_order_mark_is_tolerated(session):
    """Excel writes a BOM; without utf-8-sig the first header becomes '\\ufeffline_id' and
    every row fails for a missing line_id."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"

    result = await import_stock_take_csv(
        session, take.id, b"\xef\xbb\xbf" + _sheet(rows), dry_run=False
    )

    assert result["failed"] == []
    assert Decimal((await _lines(session, take.id))[0].counted_qty) == Decimal(8)


async def test_a_row_whose_item_does_not_match_its_line_fails_alone(session):
    """Catches a file whose rows were re-sorted or pasted from another take — the counts
    are real but would land on the wrong items."""
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[1]["counted_qty"] = "3"
    rows[1]["item_id"] = "999"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert [f["row"] for f in result["failed"]] == [3]
    assert "item_id" in result["failed"][0]["error"]
    lines = await _lines(session, take.id)
    assert Decimal(lines[0].counted_qty) == Decimal(8)
    assert lines[1].counted_qty is None


async def test_a_mismatched_item_type_fails_alone(session):
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "8"
    rows[0]["item_type"] = "material"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert [f["row"] for f in result["failed"]] == [2]
    assert (await _lines(session, take.id))[0].counted_qty is None


async def test_a_fractional_count_on_an_each_material_fails_alone(session):
    session.add(GeneralSettings(id=1))
    material = Material(
        name="Screws",
        category=legacy_value_for("hardware"),
        category_id=(await _category(session, "hardware")).id,
        unit=MaterialUnit.each,
        current_qty=Decimal(10),
    )
    session.add(material)
    await session.flush()
    session.add(MaterialAdjustment(material_id=material.id, mode="adjust", qty_delta=Decimal(10), reason="in"))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, StockTakeScope(include_materials=True))
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "2.5"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert [f["row"] for f in result["failed"]] == [2]
    assert "whole number" in result["failed"][0]["error"]


async def test_a_negative_count_fails_alone(session):
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    rows[0]["counted_qty"] = "-3"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert [f["row"] for f in result["failed"]] == [2]


async def test_a_line_from_another_take_fails_alone(session):
    take = await _fixture_take(session)
    other, _ = await stock_takes.create_stock_take(session, StockTakeScope(include_materials=True))
    rows = _rows(await export_stock_take_csv(session, other.id))
    rows[0]["counted_qty"] = "8"

    result = await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=False, on_error="skip")

    assert "not on this stock take" in result["failed"][0]["error"]


async def test_importing_into_a_closed_take_is_refused(session, pushes):
    take = await _fixture_take(session)
    rows = _rows(await export_stock_take_csv(session, take.id))
    await stock_takes.approve_stock_take(session, take.id)

    with pytest.raises(HTTPException):
        await import_stock_take_csv(session, take.id, _sheet(rows), dry_run=True)
