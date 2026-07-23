"""First-run bootstrap for the packaged desktop app.

Resolves a per-user data directory, generates credentials on first launch, points the
app at SQLite + that data directory via environment variables, migrates the database to
head, and seeds default reference data. Must run — and finish setting environment
variables — before `app.config`/`app.main` are ever imported: `Settings` is a
module-level singleton read once at import time (see app/config.py), so there is no way
to hand it configuration after the fact short of an env var already being in place.

Not used by the normal `uv run uvicorn` dev loop (which reads backend/.env directly) —
only by the packaged entrypoint (app/__main__.py).
"""

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

_CONFIG_FILENAME = "config.json"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "StockSmith"
    return Path.home() / ".stocksmith"


def config_path() -> Path:
    """Public so app/main.py's one-time /bootstrap-info handoff endpoint can read/update
    the same config.json this module writes, without re-deriving the data dir logic."""
    return data_dir() / _CONFIG_FILENAME


def _backend_root() -> Path:
    """Where alembic.ini/alembic/ live — the packaged bundle's root in a frozen build
    (PyInstaller sets sys._MEIPASS for both onefile and onedir), or this file's
    grandparent directory (backend/) when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _generate_config() -> dict:
    from app.security import hash_password

    password = secrets.token_urlsafe(18)
    return {
        "shared_password": password,
        "shared_password_hash": hash_password(password),
        "token_encryption_key": Fernet.generate_key().decode(),
        "bootstrap_info_consumed": False,
    }


def _load_or_create_config(config_path: Path) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    config = _generate_config()
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def _configure_logging(log_path: Path) -> None:
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s [%(name)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler())


def _run_migrations(db_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    backend_root = _backend_root()
    alembic_cfg = Config(str(backend_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    command.upgrade(alembic_cfg, "head")


async def _seed(db_path: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.seed import ensure_seed_data

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    session_factory = async_sessionmaker(engine)
    async with session_factory() as session:
        await ensure_seed_data(session)
    await engine.dispose()


def run() -> Path:
    """Idempotent — safe to call on every launch. Returns the resolved data directory."""
    root = data_dir()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)

    _configure_logging(root / "backend.log")
    logging.getLogger("stocksmith.bootstrap").info("Starting bootstrap (data dir: %s)", root)

    config = _load_or_create_config(config_path())

    db_path = root / "data" / "stocksmith.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    os.environ["ASSET_ROOT"] = str(root / "assets")
    os.environ["SHARED_PASSWORD_HASH"] = config["shared_password_hash"]
    os.environ["TOKEN_ENCRYPTION_KEY"] = config["token_encryption_key"]

    _run_migrations(db_path)
    asyncio.run(_seed(db_path))

    logging.getLogger("stocksmith.bootstrap").info("Bootstrap complete")
    return root
