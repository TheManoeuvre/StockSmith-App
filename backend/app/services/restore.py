"""Staging a backup for restore, and reporting what happened to the last one.

Restore is split across a process boundary on purpose. This module *validates and stages*; the
swap itself happens in bootstrap.py on the next launch, before an engine exists.

Hot-swapping the database under a running app was considered and rejected. The easy part —
late-binding the four modules that import `async_session_factory` at import time — doesn't solve
anything. The real obstacles are requests and background tasks holding open sessions mid
transaction with no way to be told to abort, Windows keeping file locks on the database and its
WAL sidecars until every handle closes, and the absence of any instant at which "the old database
is gone and the new one is here" is simultaneously true for every reader. Doing the swap before
`create_async_engine` has ever been called has none of those problems, and gets forward
migration of an older backup for free, because bootstrap already runs Alembic right there.

Everything expensive and everything fallible happens *here*, while there is a running app, a
logger and a UI to report into. What bootstrap inherits is a validated directory and a handful of
file moves, which is the least code you want executing before anything else exists.
"""

import json
import logging
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services import paths
from app.services.backup_archive import (
    ASSETS_PREFIX,
    DB_ENTRY,
    FORMAT_VERSION,
    MANIFEST_NAME,
    BackupError,
    Manifest,
    read_manifest,
)

logger = logging.getLogger("stocksmith.restore")

PENDING_MARKER = "RESTORE_PENDING.json"
LAST_RESTORE = "last-restore.json"
STAGED_DIRNAME = "staged"

# Two attempts, then give up permanently. A restore that hard-crashes the process must not turn
# into a boot loop that leaves the app permanently unusable.
MAX_ATTEMPTS = 2


class RestoreError(RuntimeError):
    """Refused. The message is user-facing."""


class RestoreVersionError(RestoreError):
    """The archive was written by a newer build than this one can safely read."""


@dataclass(frozen=True)
class LastRestore:
    state: str  # "done" | "failed"
    completed_at: str | None
    error: str | None
    source_filename: str | None
    prerestore_filename: str | None
    acknowledged: bool

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "completed_at": self.completed_at,
            "error": self.error,
            "source_filename": self.source_filename,
            "prerestore_filename": self.prerestore_filename,
            "acknowledged": self.acknowledged,
        }


def pending_marker_path() -> Path:
    return paths.restore_dir() / PENDING_MARKER


def last_restore_path() -> Path:
    return paths.restore_dir() / LAST_RESTORE


def staged_dir() -> Path:
    return paths.restore_dir() / STAGED_DIRNAME


def read_pending() -> dict | None:
    path = pending_marker_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Unreadable restore marker at %s — ignoring it", path, exc_info=True)
        return None


def read_last_restore() -> LastRestore | None:
    path = last_restore_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return LastRestore(
        state=str(raw.get("state", "done")),
        completed_at=raw.get("completed_at"),
        error=raw.get("error"),
        source_filename=raw.get("source_filename"),
        prerestore_filename=raw.get("prerestore_filename"),
        acknowledged=bool(raw.get("acknowledged", False)),
    )


def acknowledge_last_restore() -> None:
    """Dismiss the post-restore banner. Keeps the record, drops the notification."""
    record = read_last_restore()
    if record is None:
        return
    updated = LastRestore(**{**record.__dict__, "acknowledged": True})
    last_restore_path().write_text(json.dumps(updated.to_dict(), indent=2), encoding="utf-8")


def known_revisions() -> set[str]:
    """Every schema revision this build's migration scripts know about.

    Used to decide whether an archive came from a *newer* StockSmith. Membership, not a version
    string comparison: it's exact, and it can't be fooled by a build whose version was bumped
    without a migration or vice versa.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.bootstrap import _backend_root

    root = _backend_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return {revision.revision for revision in ScriptDirectory.from_config(cfg).walk_revisions()}


def _free_space_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _check_disk_space(manifest: Manifest) -> None:
    """Refuse rather than fill the disk halfway through.

    Needed: the staged copy, plus the pre-restore snapshot bootstrap will take of everything
    currently there. A 20% margin on top, because running a Windows volume to literally zero
    causes problems well beyond this app.
    """
    needed = manifest.db_bytes + manifest.asset_bytes
    db = paths.sqlite_db_path()
    current = (db.stat().st_size if db.exists() else 0) + _dir_size(paths.asset_root())
    required = int((needed + current) * 1.2)
    available = _free_space_bytes(paths.data_root())
    if available < required:
        raise RestoreError(
            f"Not enough disk space. Restoring needs about {required // (1024 * 1024)} MB free "
            f"(the backup, plus a snapshot of your current data), but only "
            f"{available // (1024 * 1024)} MB is available."
        )


def _validate_staged_database(db_path: Path) -> str | None:
    """PRAGMA integrity_check, then read the revision. Returns the revision, or None."""
    if not db_path.exists():
        raise RestoreError("The backup contains no database.")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RestoreError(f"The database in this backup is damaged ({result[0] if result else 'unknown'}).")
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return row[0] if row else None
        except sqlite3.Error:
            return None
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"The database in this backup could not be opened: {exc}") from exc
    finally:
        conn.close()


def _check_revision(revision: str | None, manifest: Manifest) -> None:
    if revision is None:
        # No alembic_version table. Bootstrap will migrate it to head, which is the right
        # outcome for a database that predates versioning.
        return
    if revision in known_revisions():
        return
    raise RestoreVersionError(
        f"This backup was made by a newer version of StockSmith ({manifest.app_version}, schema "
        f"{revision[:8]}…). Restoring it would corrupt your data. Update StockSmith to "
        f"{manifest.app_version} or later, then try again."
    )


def clear_staging() -> None:
    shutil.rmtree(staged_dir(), ignore_errors=True)
    pending_marker_path().unlink(missing_ok=True)


def stage(archive: Path, *, source_filename: str, requested_from: str) -> Manifest:
    """Validate an archive and unpack it ready for bootstrap to apply on next launch.

    Nothing here touches the live database or assets — every check runs against the extracted
    copy, so a rejected restore leaves the running app exactly as it was.
    """
    paths.ensure_dirs()

    try:
        manifest = read_manifest(archive)
    except BackupError as exc:
        # Re-raised as a RestoreError so this function has one error type in its contract. The
        # router maps RestoreError to 400; letting BackupError escape would have turned "you
        # picked a file that isn't a backup" into a 500.
        raise RestoreError(str(exc)) from exc

    if manifest.format_version > FORMAT_VERSION:
        raise RestoreVersionError(
            f"This backup uses a newer archive format (version {manifest.format_version}). "
            f"Update StockSmith and try again."
        )

    _check_disk_space(manifest)

    target = staged_dir()
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if DB_ENTRY not in names:
                raise RestoreError("The backup contains no database.")
            # Extract only entries we recognise. A zip can name a member anything, including a
            # path that climbs out of the destination; refusing unknown prefixes outright is
            # simpler and stricter than sanitising each name.
            wanted = [n for n in names if n == DB_ENTRY or n.startswith(ASSETS_PREFIX) or n == MANIFEST_NAME]
            for name in wanted:
                resolved = (target / name).resolve()
                if not str(resolved).startswith(str(target.resolve())):
                    raise RestoreError(f"The backup contains an unsafe path: {name}")
            zf.extractall(target, members=wanted)

        revision = _validate_staged_database(target / DB_ENTRY)
        _check_revision(revision, manifest)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    marker = {
        "state": "pending",
        "attempts": 0,
        "staged_dir": str(target),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_from": requested_from,
        "source_filename": source_filename,
        "manifest": manifest.to_dict(),
    }
    pending_marker_path().write_text(json.dumps(marker, indent=2), encoding="utf-8")
    logger.info("Restore staged from %s (schema %s)", source_filename, manifest.alembic_revision)
    return manifest
