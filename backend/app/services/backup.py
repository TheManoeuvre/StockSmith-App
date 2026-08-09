"""Taking, listing and pruning backups.

The async half of the backup feature: it decides *when* and *where*, and delegates the actual
archive construction to backup_archive (which is sync, so bootstrap can reuse it).
"""

import asyncio
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import engine
from app.models.backup_settings import BackupSettings
from app.services import paths
from app.services.backup_archive import BackupError, Manifest, build_archive, read_manifest

logger = logging.getLogger("stocksmith.backup")

BACKUP_PREFIX = "stocksmith-backup"
PRERESTORE_PREFIX = "stocksmith-prerestore"

# Both the name we generate and the only names we will ever accept back from a client or delete
# from a user's folder. Anchored, no path separators, no traversal.
ARCHIVE_NAME_RE = re.compile(rf"^(?:{BACKUP_PREFIX}|{PRERESTORE_PREFIX})-\d{{8}}-\d{{6}}\.zip$")

# Snapshots taken automatically before a restore are kept independently of retention_count, so a
# scheduled run can never evict the rollback point for a restore that just happened.
PRERESTORE_RETENTION = 3

_STALE_TMP_SECONDS = 3600

# Shared with the manual "Back up now" endpoint so the two can't run concurrently — same
# arrangement sync_scheduler uses for its per-platform locks. The manual endpoint reports a
# conflict rather than queueing; the scheduler skips its tick.
_lock = asyncio.Lock()


class BackupUnsupportedError(RuntimeError):
    """This backend can't be backed up by this feature (i.e. it isn't SQLite)."""


@dataclass(frozen=True)
class BackupFile:
    filename: str
    location: str  # "primary" | "secondary" | "pre-restore"
    size_bytes: int
    manifest: Manifest


def get_lock() -> asyncio.Lock:
    return _lock


def is_supported() -> bool:
    return engine.dialect.name == "sqlite"


def unsupported_reason() -> str:
    return (
        f"Backups are only available when StockSmith is running on its own SQLite database "
        f"(this backend is on {engine.dialect.name})."
    )


def _require_supported() -> None:
    if not is_supported():
        raise BackupUnsupportedError(unsupported_reason())


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sweep_stale_temp_files() -> None:
    """Remove .tmp-* snapshots orphaned by a crash mid-backup.

    Bounded by age rather than deleted unconditionally, so a sweep can never pull the file out
    from under a backup running right now.
    """
    cutoff = time.time() - _STALE_TMP_SECONDS
    for tmp in paths.backup_dir().glob(".tmp-*.db"):
        try:
            if tmp.stat().st_mtime < cutoff:
                tmp.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove stale temp snapshot %s", tmp, exc_info=True)


async def _snapshot_via_engine(target: Path) -> None:
    """VACUUM INTO through the app's own engine.

    Reuses the existing pool rather than opening a second handle on a database this process
    already has open, and runs on aiosqlite's worker thread so a large vacuum doesn't block the
    event loop.

    AUTOCOMMIT is required, not preference: SQLAlchemy opens an implicit transaction, and VACUUM
    cannot run inside one.
    """
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.exec_driver_sql("VACUUM INTO ?", (str(target),))


async def _copy_to_secondary(archive: Path, secondary: Path) -> None:
    await asyncio.to_thread(_copy_to_secondary_sync, archive, secondary)


def _copy_to_secondary_sync(archive: Path, secondary: Path) -> None:
    secondary.mkdir(parents=True, exist_ok=True)
    # Write to a temp name then rename, so a sync client (OneDrive, Dropbox) watching the folder
    # never uploads a half-written archive and never sees the final name until it's complete.
    staging = secondary / f".{archive.name}.part"
    try:
        shutil.copy2(archive, staging)
        staging.replace(secondary / archive.name)
    finally:
        staging.unlink(missing_ok=True)


def _prune(directory: Path, keep: int, prefix: str = BACKUP_PREFIX) -> list[str]:
    """Keep the newest `keep` archives matching our own naming, delete the rest.

    Only ever touches files matching the prefix pattern. The secondary directory belongs to the
    user — it may well be a general-purpose OneDrive folder — and deleting anything we didn't
    write there would be unforgivable.
    """
    if not directory.exists():
        return []
    candidates = sorted(
        (p for p in directory.glob(f"{prefix}-*.zip") if ARCHIVE_NAME_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    removed: list[str] = []
    for stale in candidates[keep:]:
        try:
            stale.unlink()
            removed.append(stale.name)
        except OSError:
            logger.warning("Could not prune old backup %s", stale, exc_info=True)
    return removed


async def get_settings_row(session: AsyncSession) -> BackupSettings:
    row = (await session.execute(select(BackupSettings).where(BackupSettings.id == 1))).scalar_one_or_none()
    if row is None:
        # Seeding normally handles this; creating it here keeps a database that predates the
        # seed entry (or a bare test database) from 500ing on the settings endpoint.
        row = BackupSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def run_backup(session: AsyncSession, *, kind: str = "manual") -> BackupFile:
    """Take one backup. Caller is responsible for holding `_lock`."""
    _require_supported()
    paths.ensure_dirs()
    _sweep_stale_temp_files()

    config = await get_settings_row(session)
    filename = f"{BACKUP_PREFIX}-{_timestamp()}.zip"
    archive_path = paths.backup_dir() / filename
    # VACUUM INTO refuses an existing target, so a unique temp name also removes any chance of
    # colliding with a previous run's leftovers.
    tmp_snapshot = paths.backup_dir() / f".tmp-{uuid.uuid4().hex}.db"

    try:
        await _snapshot_via_engine(tmp_snapshot)
        manifest = await asyncio.to_thread(
            build_archive,
            snapshot=tmp_snapshot,
            asset_root=paths.asset_root(),
            target_zip=archive_path,
            app_version=settings.app_version,
            kind=kind,
        )
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        config.last_run_at = datetime.now(timezone.utc)
        config.last_run_status = "error"
        config.last_run_error = str(exc)
        await session.commit()
        raise
    finally:
        tmp_snapshot.unlink(missing_ok=True)

    _prune(paths.backup_dir(), config.retention_count)

    if config.secondary_dir:
        try:
            secondary = Path(config.secondary_dir)
            await _copy_to_secondary(archive_path, secondary)
            await asyncio.to_thread(_prune, secondary, config.retention_count)
            config.secondary_dir_last_ok_at = datetime.now(timezone.utc)
            config.secondary_dir_last_error = None
        except Exception as exc:
            # A broken secondary must not fail the backup — the primary copy exists and is
            # valid. But it must be visible: a synced folder that quietly stopped working is
            # how someone ends up believing in off-host copies they don't have.
            logger.warning("Could not copy backup to secondary directory: %s", exc)
            config.secondary_dir_last_error = str(exc)

    config.last_run_at = datetime.now(timezone.utc)
    config.last_run_status = "ok"
    config.last_run_error = None
    await session.commit()

    return BackupFile(
        filename=filename,
        location="primary",
        size_bytes=archive_path.stat().st_size,
        manifest=manifest,
    )


def _list_dir(directory: Path, location: str, prefix: str = BACKUP_PREFIX) -> list[BackupFile]:
    if not directory.exists():
        return []
    found: list[BackupFile] = []
    for path in directory.glob(f"{prefix}-*.zip"):
        if not ARCHIVE_NAME_RE.match(path.name):
            continue
        try:
            found.append(
                BackupFile(
                    filename=path.name,
                    location=location,
                    size_bytes=path.stat().st_size,
                    manifest=read_manifest(path),
                )
            )
        except (BackupError, OSError) as exc:
            # A corrupt or half-copied archive shouldn't blank the whole list — skip it and say
            # so in the log. The user can still see and delete their good ones.
            logger.warning("Ignoring unreadable archive %s: %s", path.name, exc)
    return found


async def list_backups(session: AsyncSession) -> list[BackupFile]:
    """Everything on disk, newest first. The filesystem is the source of truth — see the
    BackupSettings docstring for why there's no table of these."""
    config = await get_settings_row(session)
    secondary = Path(config.secondary_dir) if config.secondary_dir else None

    def _collect() -> list[BackupFile]:
        found = _list_dir(paths.backup_dir(), "primary")
        found += _list_dir(paths.prerestore_dir(), "pre-restore", PRERESTORE_PREFIX)
        if secondary is not None and secondary.resolve() != paths.backup_dir().resolve():
            primary_names = {b.filename for b in found}
            found += [b for b in _list_dir(secondary, "secondary") if b.filename not in primary_names]
        return found

    found = await asyncio.to_thread(_collect)
    return sorted(found, key=lambda b: b.filename, reverse=True)


def resolve_archive(filename: str) -> Path:
    """Map a client-supplied filename onto a real archive, or refuse.

    Two independent checks, because either alone is insufficient: the pattern rejects anything
    that isn't one of our own generated names (no separators, no traversal, no `..`), and the
    resolved-parent check catches a name that passes the pattern but still lands somewhere else
    — via a symlink, or a directory that has since moved.
    """
    if not ARCHIVE_NAME_RE.match(filename):
        raise BackupError("Not a valid backup filename.")

    allowed_parents = [paths.backup_dir().resolve(), paths.prerestore_dir().resolve()]
    for parent in allowed_parents:
        candidate = (parent / filename).resolve()
        if candidate.parent == parent and candidate.is_file():
            return candidate
    raise BackupError(f"No backup named {filename}.")


def delete_backup(filename: str) -> None:
    resolve_archive(filename).unlink()


def validate_secondary_dir(raw: str) -> None:
    """Check the folder exists and we can actually write to it, by writing.

    Probing with a real file rather than checking permission bits: on Windows a OneDrive folder
    can look perfectly writable and then fail on a locked or unsynced path, and the only way to
    learn that is to try.
    """
    directory = Path(raw)
    if not directory.exists():
        raise BackupError(f"{directory} does not exist.")
    if not directory.is_dir():
        raise BackupError(f"{directory} is not a folder.")
    probe = directory / f".stocksmith-write-test-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise BackupError(f"Cannot write to {directory}: {exc}") from exc
    finally:
        probe.unlink(missing_ok=True)
