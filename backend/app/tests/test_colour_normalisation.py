"""Colour: the migration that folds free text into reference rows, and the CSV round-trip.

The migration test runs the real revision against a database seeded with the messy values free
text actually accumulates. It's the highest-risk part of this change — it runs once, against real
data, and there's no second chance to get the grouping right.
"""

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.models.colour import Colour
from app.models.material import Material, MaterialCategory, MaterialUnit
from app.services import colours
from app.services.csv_io import export_materials_csv, import_materials_csv


def _alembic_config(db_path: Path) -> Config:
    """Point Alembic at a throwaway database.

    Setting sqlalchemy.url on the Config alone is not enough: alembic/env.py overwrites it with
    `settings.database_url` (env.py:21), so without patching that too the migration runs against
    whatever the dev environment points at.
    """
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return cfg


class TestMigration:
    @pytest.fixture
    def migrated(self, tmp_path, monkeypatch):
        """A database at the revision just before colours, seeded with realistic mess."""
        db_path = tmp_path / "colours.db"
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, "d8a3f0c51e27")

        conn = sqlite3.connect(str(db_path))
        rows = [
            ("PLA Black A", "Black"),
            ("PLA Black B", "black"),
            ("PLA Black C", " BLACK "),
            ("PLA Black D", "Black"),
            ("PETG Red", "Red"),
            ("Custom", "#FF00AA"),
            ("Unspecified", None),
            ("Blank", ""),
        ]
        for name, colour in rows:
            conn.execute(
                "INSERT INTO materials (name, category, unit, current_qty, allocated_qty, "
                "reorder_threshold, avg_unit_cost, is_active, colour) "
                "VALUES (?, 'filament', 'g', 0, 0, 0, 0, 1, ?)",
                (name, colour),
            )
        conn.commit()
        conn.close()

        command.upgrade(cfg, "e6b21d84f309")
        return db_path

    def _colours(self, db_path: Path) -> dict[str, str | None]:
        conn = sqlite3.connect(str(db_path))
        try:
            return {name: hex_code for name, hex_code in conn.execute("SELECT name, hex_code FROM colours")}
        finally:
            conn.close()

    def test_case_variants_collapse_into_one_colour(self, migrated):
        """"Black", "black" and " BLACK " are one colour that free text let drift into three."""
        assert set(self._colours(migrated)) == {"Black", "Red", "#FF00AA"}

    def test_the_most_frequent_spelling_wins(self, migrated):
        # "Black" appears twice, "black" and " BLACK " once each — the majority spelling is the
        # closest thing to an intended canonical form the data actually contains.
        assert "Black" in self._colours(migrated)
        assert "black" not in self._colours(migrated)

    def test_hex_values_get_a_hex_code_and_keep_their_text_as_the_name(self, migrated):
        # The field was labelled "Colour / hex", so some values are literally hex. Inventing a
        # name for them would be worse than keeping what was typed.
        assert self._colours(migrated)["#FF00AA"] == "#ff00aa"
        assert self._colours(migrated)["Black"] is None

    def test_every_material_points_at_the_right_colour(self, migrated):
        conn = sqlite3.connect(str(migrated))
        try:
            pairs = dict(
                conn.execute(
                    "SELECT m.name, c.name FROM materials m LEFT JOIN colours c ON c.id = m.colour_id"
                )
            )
        finally:
            conn.close()

        assert pairs["PLA Black A"] == "Black"
        assert pairs["PLA Black B"] == "Black"
        assert pairs["PLA Black C"] == "Black"  # whitespace and case both handled
        assert pairs["PETG Red"] == "Red"
        assert pairs["Custom"] == "#FF00AA"
        assert pairs["Unspecified"] is None
        assert pairs["Blank"] is None

    def test_the_legacy_text_column_is_left_intact(self, migrated):
        """Not dropped: SQLite needs a table rebuild for that, and restoring an older backup and
        migrating it forward is now routine — one-way changes want to wait a release."""
        conn = sqlite3.connect(str(migrated))
        try:
            assert conn.execute("SELECT colour FROM materials WHERE name='PLA Black B'").fetchone()[0] == "black"
        finally:
            conn.close()


class TestFindOrCreate:
    async def test_matches_case_insensitively(self, session):
        """Otherwise the API would let the duplication the migration just cleaned up start over."""
        first = await colours.find_or_create(session, "Black")
        await session.commit()
        second = await colours.find_or_create(session, "  black  ")

        assert second is not None and first is not None
        assert second.id == first.id

    async def test_blank_and_none_resolve_to_nothing(self, session):
        assert await colours.find_or_create(session, None) is None
        assert await colours.find_or_create(session, "   ") is None

    async def test_creates_a_trimmed_colour(self, session):
        colour = await colours.find_or_create(session, "  Galaxy Purple ")
        assert colour is not None
        assert colour.name == "Galaxy Purple"


class TestCsvRoundTrip:
    async def _material(self, session, name: str, colour_name: str | None):
        colour = await colours.find_or_create(session, colour_name)
        session.add(
            Material(
                name=name,
                category=MaterialCategory.filament,
                unit=MaterialUnit.g,
                colour=colour.name if colour else None,
                colour_id=colour.id if colour else None,
            )
        )
        await session.commit()

    async def test_export_writes_the_colour_name_not_its_id(self, session):
        """Ids aren't portable between machines, and this file is meant to be hand-editable —
        the same reasoning as the existing manufacturer_name and material_type_name columns."""
        await self._material(session, "PLA Black", "Black")

        rows = list(csv.DictReader(io.StringIO(await export_materials_csv(session))))

        assert rows[0]["colour"] == "Black"

    async def test_import_creates_the_colour_and_links_it(self, session):
        content = (
            "name,category,unit,current_qty,reorder_threshold,colour,material_type_name,barcode,"
            "manufacturer_name,default_supplier_name,typical_reorder_qty,is_active,product_url\n"
            "PETG Blue,filament,g,0,0,Ocean Blue,,,,,,true,\n"
        ).encode()

        result = await import_materials_csv(session, content)

        assert result["created"] == 1
        material = (await session.execute(__import__("sqlalchemy").select(Material))).scalars().one()
        assert material.colour_id is not None
        colour = await session.get(Colour, material.colour_id)
        assert colour is not None and colour.name == "Ocean Blue"
        # The legacy column stays in step so a rollback to the previous release still shows it.
        assert material.colour == "Ocean Blue"

    async def test_import_reuses_an_existing_colour_regardless_of_case(self, session):
        await self._material(session, "PLA Black", "Black")
        content = (
            "name,category,unit,current_qty,reorder_threshold,colour,material_type_name,barcode,"
            "manufacturer_name,default_supplier_name,typical_reorder_qty,is_active,product_url\n"
            "PLA Black Two,filament,g,0,0,BLACK,,,,,,true,\n"
        ).encode()

        await import_materials_csv(session, content)

        assert len((await session.execute(__import__("sqlalchemy").select(Colour))).scalars().all()) == 1

    async def test_export_import_export_is_stable(self, session):
        await self._material(session, "PLA Black", "Black")
        await self._material(session, "PETG Red", "Red")

        first = await export_materials_csv(session)
        await import_materials_csv(session, first.encode())
        second = await export_materials_csv(session)

        assert first == second
