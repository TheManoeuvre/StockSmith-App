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
import shutil
import sys
import time
from datetime import datetime, timezone
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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# How long to keep retrying a move that Windows refuses because something still has the file
# open. Comfortably longer than a dying process needs to release its handles, and short enough
# that a genuinely stuck lock still reports rather than hanging the launch.
_MOVE_RETRY_SECONDS = 15.0


def _replace_with_retry(src: Path, dst: Path, log) -> None:
    """os.replace, retried while Windows says the file is in use.

    POSIX renames over an open file happily; Windows raises PermissionError (WinError 32) until
    every handle is closed. The restore swap runs moments after the previous sidecar was killed
    with `taskkill /F /T`, and process teardown is not instantaneous — so a brief window exists
    where the old process is gone but its handle on stocksmith.db is not yet released.

    Observed, not theorised: a dry run against real data with a live engine still attached failed
    exactly this way. The rollback handled it correctly and no data was lost, but the restore was
    marked failed and burned one of only two attempts — for a condition that clears itself in
    milliseconds. Retrying turns a likely-transient race into a non-event.
    """
    deadline = time.monotonic() + _MOVE_RETRY_SECONDS
    attempt = 0
    while True:
        try:
            os.replace(src, dst)
            if attempt:
                log.info("Moved %s after %d retries", src.name, attempt)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            attempt += 1
            if attempt == 1:
                log.info("%s is still in use — waiting for the previous process to release it", src.name)
            time.sleep(0.25)


def maybe_apply_staged_restore(root: Path) -> None:
    """Swap in a staged backup, if one is waiting.

    This is the only moment a whole-database restore is safe: before `create_async_engine` has
    ever run, so nothing holds a connection, no request is mid-transaction, and Windows has no
    file lock on the database or its WAL sidecars. Everything fallible — unzipping, integrity
    checking, version compatibility — already happened at stage time, while there was a running
    app to report failures into. What's left here is file moves.

    Called before `_run_migrations`, deliberately: a restored database can be older than this
    build, and the Alembic upgrade immediately below then brings it forward for free.

    Every failure path must leave a bootable app. That is the property this function is
    organised around, above speed and above completing the restore at all.
    """
    log = logging.getLogger("stocksmith.bootstrap")
    restore_root = root / "restore"
    marker_path = restore_root / "RESTORE_PENDING.json"
    record_path = restore_root / "last-restore.json"

    if not marker_path.exists():
        return

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Unreadable restore marker — removing it and booting normally", exc_info=True)
        marker_path.unlink(missing_ok=True)
        return

    if marker.get("state") != "pending":
        return

    attempts = int(marker.get("attempts", 0)) + 1
    if attempts > 2:
        # A restore that crashes the process must not become a boot loop. Give up permanently,
        # loudly, and boot on whatever data is currently in place.
        log.error("Abandoning staged restore after %d failed attempts", attempts - 1)
        marker_path.unlink(missing_ok=True)
        _write_json(
            record_path,
            {
                "state": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": "The restore was attempted twice and did not complete. Your existing data is unchanged.",
                "source_filename": marker.get("source_filename"),
                "prerestore_filename": None,
                "acknowledged": False,
            },
        )
        shutil.rmtree(restore_root / "staged", ignore_errors=True)
        return

    # Written back before anything destructive, so a hard crash mid-swap still increments.
    marker["attempts"] = attempts
    marker["state"] = "applying"
    _write_json(marker_path, marker)

    staged = Path(marker.get("staged_dir") or (restore_root / "staged"))
    staged_db = staged / "db" / "stocksmith.db"
    staged_assets = staged / "assets"
    if not staged_db.exists():
        log.error("Staged restore is missing its database at %s — abandoning", staged_db)
        marker_path.unlink(missing_ok=True)
        shutil.rmtree(staged, ignore_errors=True)
        return

    db_path = root / "data" / "stocksmith.db"
    assets_path = root / "assets"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    displaced_db = db_path.with_name(f"stocksmith.db.replaced-{stamp}")
    displaced_assets = assets_path.with_name(f"assets.replaced-{stamp}")

    log.info("Applying staged restore from %s", marker.get("source_filename"))
    prerestore_name: str | None = None

    try:
        prerestore_name = _take_prerestore_snapshot(root, db_path, assets_path, stamp, log)

        # Same volume throughout (everything lives under the one data directory), so each move
        # is atomic rather than a copy that could be interrupted halfway.
        if db_path.exists():
            _replace_with_retry(db_path, displaced_db, log)
        # Critical: a WAL left over from the *old* database would be applied to the new file
        # sitting at that name. Not a stale read — corruption.
        for sidecar in (
            db_path.with_name("stocksmith.db-wal"),
            db_path.with_name("stocksmith.db-shm"),
        ):
            sidecar.unlink(missing_ok=True)
        _replace_with_retry(staged_db, db_path, log)

        if assets_path.exists():
            _replace_with_retry(assets_path, displaced_assets, log)
        if staged_assets.exists():
            _replace_with_retry(staged_assets, assets_path, log)
        else:
            # A backup with no assets is legitimate (a brand-new shop). Give the app the empty
            # directory it expects rather than leaving nothing there.
            assets_path.mkdir(parents=True, exist_ok=True)

    except Exception as exc:
        log.exception("Staged restore failed — rolling back")
        _roll_back(db_path, displaced_db, assets_path, displaced_assets, log)
        marker_path.unlink(missing_ok=True)
        _write_json(
            record_path,
            {
                "state": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{exc}. Your existing data was left unchanged.",
                "source_filename": marker.get("source_filename"),
                "prerestore_filename": prerestore_name,
                "acknowledged": False,
            },
        )
        return

    marker_path.unlink(missing_ok=True)
    _write_json(
        record_path,
        {
            "state": "done",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
            "source_filename": marker.get("source_filename"),
            "prerestore_filename": prerestore_name,
            "acknowledged": False,
        },
    )
    shutil.rmtree(staged, ignore_errors=True)
    displaced_db.unlink(missing_ok=True)
    shutil.rmtree(displaced_assets, ignore_errors=True)
    log.info("Restore applied successfully")


def _take_prerestore_snapshot(root: Path, db_path: Path, assets_path: Path, stamp: str, log) -> str | None:
    """Archive the current data so the restore can be undone.

    Best-effort by design: if there is nothing to snapshot, or snapshotting fails, the restore
    still proceeds. Refusing to restore because the *undo* couldn't be prepared would be the
    wrong trade — the user asked for the restore, and the backup they're restoring from exists.
    """
    if not db_path.exists():
        return None
    try:
        from app.services.backup_archive import build_archive, snapshot_database

        prerestore_dir = root / "backups" / "pre-restore"
        prerestore_dir.mkdir(parents=True, exist_ok=True)
        tmp_snapshot = prerestore_dir / f".tmp-prerestore-{stamp}.db"
        tmp_snapshot.unlink(missing_ok=True)
        # VACUUM INTO, not a copy — it folds the WAL in, which a copy would leave behind.
        snapshot_database(db_path, tmp_snapshot)
        try:
            name = f"stocksmith-prerestore-{stamp}.zip"
            build_archive(
                snapshot=tmp_snapshot,
                asset_root=assets_path,
                target_zip=prerestore_dir / name,
                app_version=os.environ.get("STOCKSMITH_APP_VERSION", "dev"),
                kind="pre-restore",
            )
            _prune_prerestore(prerestore_dir)
            return name
        finally:
            tmp_snapshot.unlink(missing_ok=True)
    except Exception:
        log.warning("Could not take a pre-restore snapshot — continuing with the restore", exc_info=True)
        return None


def _prune_prerestore(directory: Path, keep: int = 3) -> None:
    archives = sorted(directory.glob("stocksmith-prerestore-*.zip"), reverse=True)
    for stale in archives[keep:]:
        stale.unlink(missing_ok=True)


def _roll_back(db_path: Path, displaced_db: Path, assets_path: Path, displaced_assets: Path, log) -> None:
    """Put back whatever was moved aside. Each step guarded separately so one failure doesn't
    prevent the others — a partial rollback still beats none."""
    try:
        if displaced_db.exists():
            db_path.unlink(missing_ok=True)
            os.replace(displaced_db, db_path)
    except Exception:
        log.exception("Could not roll back the database file")
    try:
        if displaced_assets.exists():
            if assets_path.exists():
                shutil.rmtree(assets_path, ignore_errors=True)
            os.replace(displaced_assets, assets_path)
    except Exception:
        log.exception("Could not roll back the assets directory")


def run() -> Path:
    """Idempotent — safe to call on every launch. Returns the resolved data directory."""
    root = data_dir()
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "backups").mkdir(parents=True, exist_ok=True)
    (root / "restore").mkdir(parents=True, exist_ok=True)

    _configure_logging(root / "backend.log")
    logging.getLogger("stocksmith.bootstrap").info("Starting bootstrap (data dir: %s)", root)

    config = _load_or_create_config(config_path())

    db_path = root / "data" / "stocksmith.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    os.environ["ASSET_ROOT"] = str(root / "assets")
    os.environ["SHARED_PASSWORD_HASH"] = config["shared_password_hash"]
    os.environ["TOKEN_ENCRYPTION_KEY"] = config["token_encryption_key"]

    # Before migrations, and before any engine exists — see the docstring. A restored database
    # older than this build is brought forward by the upgrade immediately below.
    maybe_apply_staged_restore(root)

    _run_migrations(db_path)
    asyncio.run(_seed(db_path))

    logging.getLogger("stocksmith.bootstrap").info("Bootstrap complete")
    return root
