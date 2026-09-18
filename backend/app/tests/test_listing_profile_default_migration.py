"""The migration that removes the platform-default listing profile.

Every product that was resolving to a default has to come out the other side pointed at
that same profile explicitly — otherwise an upgrade would silently turn a draftable
product into "No listing profile applies". Driven through Alembic against a throwaway
database at the revision before, because the behaviour under test is the data step, not
the model.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

BEFORE = "a9f3c2d7e815"
AFTER = "b7e4d1c92a05"


def _alembic_config(db_path: Path) -> Config:
    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    return cfg


def _product(conn: sqlite3.Connection, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO products (name, is_active, current_stock, allocated_qty, is_bundle, pricing_mode) "
        "VALUES (?, 1, 0, 0, 0, 'manual')",
        (name,),
    )
    return cur.lastrowid


def _profile(conn: sqlite3.Connection, platform: str, name: str, is_default: bool) -> int:
    cur = conn.execute(
        "INSERT INTO listing_profiles (platform, name, is_default) VALUES (?, ?, ?)",
        (platform, name, 1 if is_default else 0),
    )
    return cur.lastrowid


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    db_path = tmp_path / "profiles.db"
    monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, BEFORE)

    conn = sqlite3.connect(str(db_path))
    etsy_default = _profile(conn, "etsy", "Handmade", True)
    etsy_other = _profile(conn, "etsy", "Vintage", False)
    # eBay has profiles but no default: nothing on eBay was resolving to anything.
    _profile(conn, "ebay", "Bricks", False)

    relying_no_row = _product(conn, "No settings row")
    relying_null = _product(conn, "Row with no profile")
    chosen = _product(conn, "Chose Vintage")
    conn.execute(
        "INSERT INTO product_platform_settings (product_id, platform, listing_profile_id, listing_title) "
        "VALUES (?, 'etsy', NULL, 'Kept title')",
        (relying_null,),
    )
    conn.execute(
        "INSERT INTO product_platform_settings (product_id, platform, listing_profile_id) VALUES (?, 'etsy', ?)",
        (chosen, etsy_other),
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, AFTER)
    return db_path, dict(
        etsy_default=etsy_default,
        etsy_other=etsy_other,
        relying_no_row=relying_no_row,
        relying_null=relying_null,
        chosen=chosen,
    )


def _settings(db_path: Path) -> dict[tuple[int, str], tuple[int | None, str | None]]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT product_id, platform, listing_profile_id, listing_title FROM product_platform_settings"
    ).fetchall()
    conn.close()
    return {(product_id, platform): (profile_id, title) for product_id, platform, profile_id, title in rows}


def test_products_relying_on_the_default_are_pointed_at_it_explicitly(migrated):
    db_path, ids = migrated
    settings = _settings(db_path)

    assert settings[(ids["relying_no_row"], "etsy")] == (ids["etsy_default"], None)
    # The row is updated in place, so the listing copy on it survives.
    assert settings[(ids["relying_null"], "etsy")] == (ids["etsy_default"], "Kept title")


def test_an_explicit_choice_is_left_alone(migrated):
    db_path, ids = migrated
    assert _settings(db_path)[(ids["chosen"], "etsy")] == (ids["etsy_other"], None)


def test_a_platform_without_a_default_gets_no_rows(migrated):
    db_path, _ = migrated
    assert not any(platform == "ebay" for _, platform in _settings(db_path))


def test_the_flag_and_its_index_are_gone(migrated):
    db_path, _ = migrated
    conn = sqlite3.connect(str(db_path))
    columns = [row[1] for row in conn.execute("PRAGMA table_info(listing_profiles)")]
    indexes = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'listing_profiles'")]
    conn.close()
    assert "is_default" not in columns
    assert "uq_listing_profiles_platform_default" not in indexes
