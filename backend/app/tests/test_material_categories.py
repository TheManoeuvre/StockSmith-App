"""Material categories: the migration that turns the enum into rows, and the behaviour flags.

The migration test matters most. It runs once against real data, and a material that fails to
resolve lands in a state the app now tolerates forever — a NULL category_id quietly falling back
to the legacy column.

The behaviour tests are the ones that prove the point of the exercise: they use a *user-created*
category, not one of the seven, so they fail if any of the old `== filament` checks survived.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import select

from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.material_category import MaterialCategory
from app.routers import material_categories as material_categories_router
from app.routers._reference_crud import MergeRequest
from app.schemas.material_category import MaterialCategoryReorder, MaterialCategoryUpdate
from app.services import material_categories


def _alembic_config(db_path: Path) -> Config:
    """Point Alembic at a throwaway database.

    Setting sqlalchemy.url on the Config alone is not enough: alembic/env.py overwrites it with
    `settings.database_url`, so without patching that too the migration runs against whatever
    the dev environment points at.
    """
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return cfg


# Pinned rather than "head": this asserts what one revision did, and a later migration changing
# the answer should be a decision, not a surprise.
_BEFORE = "d4e8b1c73f92"
_AFTER = "f2a91c4d7b08"


class TestMigration:
    @pytest.fixture
    def migrated(self, tmp_path, monkeypatch):
        """A database at the revision before categories, holding one material per legacy value."""
        db_path = tmp_path / "categories.db"
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, _BEFORE)

        conn = sqlite3.connect(str(db_path))
        for category in (c.value for c in LegacyMaterialCategory):
            conn.execute(
                "INSERT INTO materials (name, category, unit, current_qty, allocated_qty, "
                "reorder_threshold, avg_unit_cost, is_active) "
                "VALUES (?, ?, 'g', 0, 0, 0, 0, 1)",
                (f"Sample {category}", category),
            )
        conn.commit()
        conn.close()

        command.upgrade(cfg, _AFTER)
        return db_path

    def _rows(self, db_path: Path) -> dict[str, tuple]:
        conn = sqlite3.connect(str(db_path))
        try:
            return {
                row[0]: row[1:]
                for row in conn.execute(
                    "SELECT name, sort_order, default_unit, consumed_on_failed_build, "
                    "auto_kitting_per_order, tracks_colour, tracks_material_type, "
                    "cost_per_kg_display FROM material_categories"
                )
            }
        finally:
            conn.close()

    def test_seeds_the_seven_original_categories(self, migrated):
        assert set(self._rows(migrated)) == {
            "filament",
            "resin",
            "pigment",
            "hardware",
            "packaging",
            "blanks",
            "other",
        }

    def test_sort_order_preserves_the_original_list_order(self, migrated):
        """Not alphabetical. The materials page listed filament first and other last on purpose,
        and alphabetising it would have been a visible regression dressed up as a refactor."""
        rows = self._rows(migrated)
        by_position = sorted(rows, key=lambda name: rows[name][0])
        assert by_position == ["filament", "resin", "pigment", "hardware", "packaging", "blanks", "other"]

    def test_flags_reproduce_the_behaviour_that_was_hardcoded(self, migrated):
        rows = self._rows(migrated)
        # Tuple is (sort_order, default_unit, failed_build, kitting, colour, material_type, per_kg).
        assert rows["filament"][2:] == (1, 0, 1, 1, 1)
        assert rows["packaging"][2:] == (0, 1, 0, 0, 0)
        # Everything else was only ever a label.
        for name in ("resin", "pigment", "hardware", "blanks", "other"):
            assert rows[name][2:] == (0, 0, 0, 0, 0), name

    def test_default_unit_matches_the_old_auto_each_list(self, migrated):
        rows = self._rows(migrated)
        each = sorted(name for name in rows if rows[name][1] == "each")
        assert each == ["blanks", "hardware", "packaging"]

    def test_every_material_resolves_to_a_category(self, migrated):
        """The backfill is an exact string match, not a case-insensitive fold like colours
        needed — the CHECK constraint on the legacy column guarantees the values line up."""
        conn = sqlite3.connect(str(migrated))
        try:
            assert conn.execute("SELECT COUNT(*) FROM materials WHERE category_id IS NULL").fetchone()[0] == 0
            mismatched = conn.execute(
                "SELECT COUNT(*) FROM materials m JOIN material_categories c ON c.id = m.category_id "
                "WHERE c.name <> m.category"
            ).fetchone()[0]
            assert mismatched == 0
        finally:
            conn.close()

    def test_the_legacy_column_is_left_alone(self, migrated):
        """Deliberately not dropped this release — an older backup restored and migrated forward
        is a routine operation, so one-way changes wait."""
        conn = sqlite3.connect(str(migrated))
        try:
            assert conn.execute("SELECT COUNT(*) FROM materials WHERE category IS NULL").fetchone()[0] == 0
        finally:
            conn.close()

    def test_the_foreign_key_restricts_rather_than_blanking(self, migrated):
        """Every other reference FK is ON DELETE SET NULL. This one must not be: a material with
        no category is not a state the app can render."""
        conn = sqlite3.connect(str(migrated))
        try:
            fks = {row[2]: row[6] for row in conn.execute("PRAGMA foreign_key_list('materials')")}
            assert fks["material_categories"] == "RESTRICT"
        finally:
            conn.close()

    def test_downgrade_removes_the_table_and_column(self, migrated, monkeypatch):
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{migrated.as_posix()}")
        command.downgrade(_alembic_config(migrated), _BEFORE)
        conn = sqlite3.connect(str(migrated))
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='material_categories'"
            ).fetchone()[0]
            assert remaining == 0
            assert "category_id" not in [row[1] for row in conn.execute("PRAGMA table_info('materials')")]
            # Nothing to recover: the legacy column was never written to.
            assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 7
        finally:
            conn.close()


class TestLegacyValue:
    def test_a_known_category_keeps_its_own_name(self):
        assert material_categories.legacy_value_for("packaging") is LegacyMaterialCategory.packaging

    def test_a_user_created_category_falls_back_to_other(self):
        """The legacy column's CHECK accepts exactly the original seven, so there is nothing else
        it can hold. The cost is that the previous release shows such a material as "other",
        which is why dropping the column is next release's job."""
        assert material_categories.legacy_value_for("Cardstock") is LegacyMaterialCategory.other


class TestFindOrCreate:
    async def test_matches_an_existing_category_ignoring_case(self, session):
        found = await material_categories.find_or_create(session, "FILAMENT")
        assert found.name == "filament"

    async def test_a_new_name_is_created_after_the_last_one(self, session):
        """Categories arrive from a name-only form and from CSV imports, neither of which has an
        opinion about position. Without this they would all sit on 0 and sort arbitrarily."""
        created = await material_categories.find_or_create(session, "Cardstock")
        await session.commit()
        assert created.sort_order == 80
        assert created.name == "Cardstock"

    async def test_a_blank_name_resolves_to_nothing(self, session):
        assert await material_categories.find_or_create(session, "   ") is None


class TestResolveUpdates:
    async def test_a_name_becomes_an_id_and_a_legal_legacy_value(self, session):
        updates = await material_categories.resolve_updates(session, {"category": "Cardstock"})
        assert updates["category"] is LegacyMaterialCategory.other
        assert updates["category_id"] is not None

    async def test_an_id_still_sets_the_legacy_value(self, session):
        packaging = await material_categories.find_or_create(session, "packaging")
        updates = await material_categories.resolve_updates(session, {"category_id": packaging.id})
        assert updates["category"] is LegacyMaterialCategory.packaging


class TestCategoryFlag:
    async def test_reads_the_flag_off_the_reference_row(self, session):
        category = MaterialCategory(name="Cardstock", sort_order=80, consumed_on_failed_build=True)
        session.add(category)
        await session.flush()
        material = Material(
            name="Card A6",
            category=LegacyMaterialCategory.other,
            category_id=category.id,
            unit=MaterialUnit.each,
        )
        session.add(material)
        await session.commit()
        await session.refresh(material)
        assert material_categories.category_flag(material, "consumed_on_failed_build") is True

    async def test_falls_back_to_the_old_behaviour_when_there_is_no_reference_row(self, session):
        """A row written by an older release mid-transition. It behaved like filament before the
        upgrade and should keep behaving like filament after it."""
        material = Material(name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g)
        session.add(material)
        await session.commit()
        await session.refresh(material)
        assert material.category_id is None
        assert material_categories.category_flag(material, "consumed_on_failed_build") is True
        assert material_categories.category_flag(material, "auto_kitting_per_order") is False


class TestRouterGuards:
    """The endpoint bodies are plain async functions, so they can be driven with a session
    directly — no HTTP, no auth, no app fixture."""

    async def test_the_last_category_cannot_be_deleted(self, session):
        """delete_if_unused can't catch this on its own: with no materials at all, the last
        category is legitimately unused. But every material needs one, so deleting it leaves a
        state with no way back through the UI."""
        for row in (await session.execute(select(MaterialCategory))).scalars().all():
            await session.delete(row)
        await session.commit()
        only = MaterialCategory(name="filament", sort_order=10)
        session.add(only)
        await session.commit()

        with pytest.raises(HTTPException) as caught:
            await material_categories_router.delete_material_category(only.id, session)
        assert caught.value.status_code == 409

    async def test_a_category_in_use_cannot_be_deleted(self, session):
        packaging = await material_categories.find_or_create(session, "packaging")
        session.add(
            Material(
                name="Box",
                category=LegacyMaterialCategory.packaging,
                category_id=packaging.id,
                unit=MaterialUnit.each,
            )
        )
        await session.commit()

        with pytest.raises(HTTPException) as caught:
            await material_categories_router.delete_material_category(packaging.id, session)
        assert caught.value.status_code == 409
        assert "1 material" in caught.value.detail

    async def test_reorder_rewrites_positions_from_the_given_sequence(self, session):
        rows = await material_categories_router.list_material_categories(session)
        reversed_ids = [row.id for row in reversed(rows)]

        after = await material_categories_router.reorder_material_categories(
            MaterialCategoryReorder(ids=reversed_ids), session
        )
        assert [row.id for row in after] == reversed_ids
        assert [row.sort_order for row in after] == [10, 20, 30, 40, 50, 60, 70]

    async def test_reorder_rejects_an_unknown_id(self, session):
        with pytest.raises(HTTPException) as caught:
            await material_categories_router.reorder_material_categories(
                MaterialCategoryReorder(ids=[9999]), session
            )
        assert caught.value.status_code == 400

    async def test_renaming_a_category_out_of_the_legacy_vocabulary_rewrites_its_materials(self, session):
        """The legacy column has to stay legal. Calling filament "Filament PLA" means its
        materials can no longer store 'filament' there, and nothing but this knows that."""
        filament = await material_categories.find_or_create(session, "filament")
        material = Material(
            name="PLA Black",
            category=LegacyMaterialCategory.filament,
            category_id=filament.id,
            unit=MaterialUnit.g,
        )
        session.add(material)
        await session.commit()

        await material_categories_router.update_material_category(
            filament.id, MaterialCategoryUpdate(name="Filament PLA"), session
        )
        await session.refresh(material)
        assert material.category is LegacyMaterialCategory.other
        assert material.category_name == "Filament PLA"

    async def test_merging_repoints_materials_and_fixes_the_legacy_column(self, session):
        filament = await material_categories.find_or_create(session, "filament")
        resin = await material_categories.find_or_create(session, "resin")
        material = Material(
            name="PLA Black",
            category=LegacyMaterialCategory.filament,
            category_id=filament.id,
            unit=MaterialUnit.g,
        )
        session.add(material)
        await session.commit()

        await material_categories_router.merge_material_category(
            filament.id, MergeRequest(target_id=resin.id), session
        )
        await session.refresh(material)
        assert material.category_id == resin.id
        assert material.category is LegacyMaterialCategory.resin

    async def test_a_flag_can_be_turned_off(self, session):
        """The regression test for patch_row's exclude_unset and rename's unconditional set —
        under the old behaviour False was indistinguishable from "not sent" and was dropped."""
        filament = await material_categories.find_or_create(session, "filament")
        await session.commit()
        assert filament.consumed_on_failed_build is True

        updated = await material_categories_router.update_material_category(
            filament.id,
            MaterialCategoryUpdate(name="filament", consumed_on_failed_build=False),
            session,
        )
        assert updated.consumed_on_failed_build is False
