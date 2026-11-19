"""First-launch business presets: the picker's data, applying a choice, and the migration that keeps
existing installs from ever being asked.

The migration test is the one that protects real users. Getting it wrong means an existing shop is
shown "what do you make?" — and if it had no materials yet, has its categories replaced.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import select

from app.models.general_settings import GeneralSettings
from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.material_type import MaterialType
from app.models.material_unit import MaterialUnitRow
from app.routers import fee_config
from app.schemas.business_type import BusinessTypeUpdate
from app.seed import _ensure_general_settings
from app.services.business_presets import PRESETS, PRESETS_BY_KEY
from app.services.material_categories import DEFAULT_CATEGORIES
from app.services.units import DEFAULT_UNITS


class TestPresetDefinitions:
    def test_five_business_types_plus_a_fallback(self):
        assert [p.key for p in PRESETS] == [
            "3d_printing",
            "candles_soap",
            "jewellery",
            "textiles",
            "paper_print",
            "general",
        ]

    def test_3d_printing_is_exactly_the_original_seven(self):
        """Existing installs are stamped with this preset, so it must describe what they have."""
        original = [{k: v for k, v in c.items() if k != "sort_order"} for c in DEFAULT_CATEGORIES]
        assert list(PRESETS_BY_KEY["3d_printing"].categories) == original

    @pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
    def test_every_default_unit_exists(self, preset):
        available = {u["name"] for u in DEFAULT_UNITS} | {u["name"] for u in preset.extra_units}
        assert {c["default_unit"] for c in preset.categories} <= available

    @pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
    def test_every_preset_has_auto_kitted_packaging(self, preset):
        """Kitting only works if some category is flagged for it; a preset without one would
        silently leave packaging out of every order's stock and cost."""
        assert any(c.get("auto_kitting_per_order") for c in preset.categories)

    @pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
    def test_category_names_are_unique_and_lowercase(self, preset):
        names = [c["name"] for c in preset.categories]
        assert len(names) == len(set(names))
        assert all(n == n.lower() for n in names)


async def _fresh(session):
    await _ensure_general_settings(session)
    await session.commit()


class TestApplyPreset:
    async def test_fresh_install_is_asked(self, session):
        await _fresh(session)
        state = await fee_config.get_business_type(session)
        assert state.business_type is None
        assert state.can_choose is True
        assert len(state.presets) == 6

    async def test_choosing_replaces_categories_and_adds_units_and_types(self, session):
        await _fresh(session)
        state = await fee_config.set_business_type(BusinessTypeUpdate(business_type="textiles"), session)

        assert state.business_type == "textiles"
        assert state.can_choose is False
        categories = (await session.scalars(select(MaterialCategory).order_by(MaterialCategory.sort_order))).all()
        assert [c.name for c in categories] == [c["name"] for c in PRESETS_BY_KEY["textiles"].categories]
        assert "filament" not in {c.name for c in categories}
        fabric = next(c for c in categories if c.name == "fabric")
        assert (fabric.default_unit, fabric.tracks_colour, fabric.consumed_on_failed_build) == ("m", True, True)

        units = {u.name: u for u in (await session.scalars(select(MaterialUnitRow))).all()}
        assert set(units) == {"g", "ml", "each", "m"}
        assert units["m"].decimal_places == 2

        types = set((await session.scalars(select(MaterialType.name))).all())
        assert {"Cotton", "Wool"} <= types

    async def test_keeps_material_types_the_user_already_added(self, session):
        await _fresh(session)
        session.add(MaterialType(name="cotton"))
        await session.commit()
        await fee_config.set_business_type(BusinessTypeUpdate(business_type="textiles"), session)
        names = (await session.scalars(select(MaterialType.name))).all()
        assert [n for n in names if n.casefold() == "cotton"] == ["cotton"]

    async def test_refused_once_chosen(self, session):
        await _fresh(session)
        await fee_config.set_business_type(BusinessTypeUpdate(business_type="jewellery"), session)
        with pytest.raises(HTTPException) as exc:
            await fee_config.set_business_type(BusinessTypeUpdate(business_type="candles_soap"), session)
        assert exc.value.status_code == 409
        names = set((await session.scalars(select(MaterialCategory.name))).all())
        assert "beads" in names and "wax" not in names

    async def test_refused_when_materials_exist(self, session):
        await _fresh(session)
        filament = (await session.scalars(select(MaterialCategory).where(MaterialCategory.name == "filament"))).one()
        session.add(Material(name="Black PLA", category="filament", category_id=filament.id, unit="g"))
        await session.commit()

        state = await fee_config.get_business_type(session)
        assert state.can_choose is False
        with pytest.raises(HTTPException) as exc:
            await fee_config.set_business_type(BusinessTypeUpdate(business_type="candles_soap"), session)
        assert exc.value.status_code == 409
        settings = await session.get(GeneralSettings, 1)
        assert settings.business_type is None

    async def test_unknown_key_is_rejected(self, session):
        await _fresh(session)
        with pytest.raises(HTTPException) as exc:
            await fee_config.set_business_type(BusinessTypeUpdate(business_type="pottery"), session)
        assert exc.value.status_code == 400


# Pinned rather than "head", for the same reason as test_material_categories.py.
_BEFORE = "c7d3a915e284"
_AFTER = "d2e7a4c19f60"


def _alembic_config(db_path: Path) -> Config:
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return cfg


class TestMigration:
    def _upgrade(self, tmp_path, monkeypatch, *, with_material: bool) -> str | None:
        db_path = tmp_path / "business_type.db"
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, _BEFORE)

        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO general_settings (id, default_currency) VALUES (1, 'GBP')")
        if with_material:
            conn.execute(
                "INSERT INTO materials (name, category, unit, current_qty, allocated_qty, "
                "reorder_threshold, avg_unit_cost, is_active) "
                "VALUES ('Black PLA', 'filament', 'g', 0, 0, 0, 0, 1)"
            )
        conn.commit()
        conn.close()

        command.upgrade(cfg, _AFTER)
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT business_type FROM general_settings WHERE id = 1").fetchone()[0]
        finally:
            conn.close()

    def test_existing_install_with_data_is_never_asked(self, tmp_path, monkeypatch):
        assert self._upgrade(tmp_path, monkeypatch, with_material=True) == "3d_printing"

    def test_install_with_no_data_is_asked(self, tmp_path, monkeypatch):
        assert self._upgrade(tmp_path, monkeypatch, with_material=False) is None
