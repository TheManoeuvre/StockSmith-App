"""The setup checklist: each step reads "done" exactly when the thing it asks for exists."""

from decimal import Decimal
from pathlib import Path

import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.models.backup_settings import BackupSettings
from app.models.kitting import DefaultKittingMaterial
from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.product import Product, ProductMaterial
from app.routers import onboarding as onboarding_router
from app.seed import _ensure_backup_settings, _ensure_general_settings


async def _status(session) -> dict[str, bool]:
    result = await onboarding_router.get_setup_status(session)
    return {s.key: s.done for s in result.steps}


async def _fresh(session):
    await _ensure_general_settings(session)
    await _ensure_backup_settings(session)
    await session.commit()


async def _material(session, qty: str = "0") -> Material:
    category = (await session.scalars(select(MaterialCategory).where(MaterialCategory.name == "packaging"))).one()
    m = Material(name=f"Mailer {qty}", category="packaging", category_id=category.id, unit="each", current_qty=Decimal(qty))
    session.add(m)
    await session.commit()
    return m


async def test_fresh_install_has_nothing_done(session):
    await _fresh(session)
    result = await onboarding_router.get_setup_status(session)
    assert [s.key for s in result.steps] == [
        "business_type",
        "materials",
        "opening_stock",
        "kitting",
        "products",
        "orders",
        "offsite_backup",
    ]
    assert not any(s.done for s in result.steps)
    assert result.dismissed is False and result.complete is False
    assert {s.key for s in result.steps if s.optional} == {"kitting", "orders", "offsite_backup"}


async def test_a_material_with_no_stock_is_not_an_opening_count(session):
    await _fresh(session)
    await _material(session, "0")
    status = await _status(session)
    assert status["materials"] is True
    assert status["opening_stock"] is False

    await _material(session, "5")
    assert (await _status(session))["opening_stock"] is True


async def test_kitting_products_and_backup_steps(session):
    await _fresh(session)
    mailer = await _material(session, "5")

    product = Product(name="Candle", sku="C-1", current_stock=0, allocated_qty=0)
    session.add(product)
    await session.commit()
    # A product with no BOM isn't the step: the step is about knowing what it's made of.
    assert (await _status(session))["products"] is False

    session.add(ProductMaterial(product_id=product.id, material_id=mailer.id, qty_required=Decimal(1)))
    session.add(DefaultKittingMaterial(material_id=mailer.id, qty_required=Decimal(1)))
    backup = await session.get(BackupSettings, 1)
    backup.secondary_dir = "C:/Users/me/OneDrive/StockSmith"
    await session.commit()

    status = await _status(session)
    assert status["products"] and status["kitting"] and status["offsite_backup"]


async def test_dismissing_and_bringing_back(session):
    await _fresh(session)
    result = await onboarding_router.update_checklist(onboarding_router.ChecklistUpdate(dismissed=True), session)
    assert result.dismissed is True
    result = await onboarding_router.update_checklist(onboarding_router.ChecklistUpdate(dismissed=False), session)
    assert result.dismissed is False


def test_migration_dismisses_the_checklist_only_for_installs_with_data(tmp_path, monkeypatch):
    def run(with_material: bool) -> int:
        db_path = tmp_path / f"checklist-{with_material}.db"
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        root = Path(__file__).resolve().parent.parent.parent
        cfg = Config(str(root / "alembic.ini"))
        cfg.set_main_option("script_location", str(root / "alembic"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
        command.upgrade(cfg, "c7d3a915e284")
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO general_settings (id, default_currency) VALUES (1, 'GBP')")
        if with_material:
            conn.execute(
                "INSERT INTO materials (name, category, unit, current_qty, allocated_qty, "
                "reorder_threshold, avg_unit_cost, is_active) VALUES ('PLA', 'filament', 'g', 0, 0, 0, 0, 1)"
            )
        conn.commit()
        conn.close()
        command.upgrade(cfg, "e8b3f0a2c614")
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT setup_checklist_dismissed FROM general_settings").fetchone()[0]
        finally:
            conn.close()

    assert run(with_material=True) == 1
    assert run(with_material=False) == 0
