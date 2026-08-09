"""Building and reading backup archives.

Deliberately synchronous, and deliberately free of any SQLAlchemy or app-config imports beyond
what it is handed. Two callers need it: the async backup service, which wraps it in
`asyncio.to_thread`, and bootstrap.py's pre-restore snapshot, which runs before an engine or an
event loop exists. One implementation, two callers, no async duplicate to keep in step.

Archive layout:

    manifest.json
    db/stocksmith.db
    assets/<mirror of ASSET_ROOT>

The snapshot inside `db/` must be produced by `VACUUM INTO`, never by copying the live file.
In WAL mode (which this app runs in — see db.configure_sqlite_pragmas) recent commits live in
the `-wal` sidecar, so a plain copy of `stocksmith.db` silently omits them. `VACUUM INTO` writes
a consistent, fully-checkpointed database in one file while readers and writers carry on.
"""

import json
import logging
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("stocksmith.backup")

MANIFEST_NAME = "manifest.json"
DB_ENTRY = "db/stocksmith.db"
ASSETS_PREFIX = "assets/"

# Bumped only for a change that an older build could not read correctly. The restore path
# refuses anything higher than it understands.
FORMAT_VERSION = 1

# VACUUM INTO landed in SQLite 3.27. Python 3.13 ships far past that, but failing with a named
# error beats whatever a missing-syntax error would look like three layers up.
_MIN_SQLITE = (3, 27, 0)

# Tables worth counting in the manifest — enough for a human to recognise which backup they're
# looking at in a list, without turning the manifest into a schema dump.
_COUNT_TABLES = ("products", "product_variants", "materials", "orders", "purchases", "builds")


class BackupError(RuntimeError):
    """Backup could not be produced. The message is user-facing."""


@dataclass(frozen=True)
class Manifest:
    format_version: int
    app_version: str
    alembic_revision: str | None
    created_at: str
    kind: str
    db_bytes: int
    asset_file_count: int
    asset_bytes: int
    counts: dict[str, int]
    includes_config: bool
    skipped_assets: list[str]
    db_instance_id: str | None

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "app_version": self.app_version,
            "alembic_revision": self.alembic_revision,
            "created_at": self.created_at,
            "kind": self.kind,
            "db_bytes": self.db_bytes,
            "asset_file_count": self.asset_file_count,
            "asset_bytes": self.asset_bytes,
            "counts": self.counts,
            "includes_config": self.includes_config,
            "skipped_assets": self.skipped_assets,
            "db_instance_id": self.db_instance_id,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Manifest":
        return cls(
            format_version=int(raw.get("format_version", 0)),
            app_version=str(raw.get("app_version", "unknown")),
            alembic_revision=raw.get("alembic_revision"),
            created_at=str(raw.get("created_at", "")),
            kind=str(raw.get("kind", "manual")),
            db_bytes=int(raw.get("db_bytes", 0)),
            asset_file_count=int(raw.get("asset_file_count", 0)),
            asset_bytes=int(raw.get("asset_bytes", 0)),
            counts=dict(raw.get("counts") or {}),
            includes_config=bool(raw.get("includes_config", False)),
            skipped_assets=list(raw.get("skipped_assets") or []),
            db_instance_id=raw.get("db_instance_id"),
        )


def assert_vacuum_into_supported() -> None:
    if sqlite3.sqlite_version_info < _MIN_SQLITE:
        raise BackupError(
            f"SQLite {'.'.join(map(str, _MIN_SQLITE))} or newer is required to take a backup "
            f"(this build has {sqlite3.sqlite_version})."
        )


def snapshot_database(db_path: Path, target: Path) -> None:
    """`VACUUM INTO` the database at db_path to target, using a plain sqlite3 connection.

    Used by bootstrap's pre-restore snapshot, where no engine exists. The running app takes its
    snapshot through the async engine instead (see services/backup.py) so it shares the
    connection pool and doesn't open a second handle on a database it already has open.

    target must not exist — VACUUM INTO refuses to overwrite, which is a feature: it means a
    half-written previous attempt can never be silently mistaken for a complete one.
    """
    assert_vacuum_into_supported()
    if target.exists():
        raise BackupError(f"Snapshot target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        # Parameterised rather than interpolated: VACUUM INTO takes an SQL expression, and a
        # data directory under a name like O'Brien would otherwise break the quoting.
        conn.execute("VACUUM INTO ?", (str(target),))
    finally:
        conn.close()


def read_snapshot_facts(snapshot: Path) -> tuple[str | None, dict[str, int], str | None]:
    """(alembic_revision, row counts, db_instance_id) read from the snapshot itself.

    From the snapshot rather than the live database on purpose — the archive should describe
    what is actually in it. Reading the revision from Alembic's ScriptDirectory instead would
    answer a different question ("what does this build know about") and is exactly the
    conflation that makes a restore downgrade-check unsound.
    """
    conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        revision: str | None = None
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
        except sqlite3.Error:
            # A database built by create_all (the test suite) has no alembic_version table.
            pass

        counts: dict[str, int] = {}
        for table in _COUNT_TABLES:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                continue

        instance_id: str | None = None
        try:
            row = conn.execute("SELECT db_instance_id FROM general_settings WHERE id = 1").fetchone()
            instance_id = row[0] if row else None
        except sqlite3.Error:
            pass

        return revision, counts, instance_id
    finally:
        conn.close()


def _iter_asset_files(asset_root: Path):
    if not asset_root.exists():
        return
    for path in sorted(asset_root.rglob("*")):
        if path.is_file():
            yield path


def build_archive(
    *,
    snapshot: Path,
    asset_root: Path,
    target_zip: Path,
    app_version: str,
    kind: str,
) -> Manifest:
    """Zip a database snapshot plus the asset tree, with a manifest describing both.

    Assets can change underneath this — a shop icon being rewritten mid-run is the realistic
    case. A file that vanishes between enumeration and read is recorded in `skipped_assets` and
    does not fail the backup: an archive missing one thumbnail is worth vastly more than no
    archive at all. A missing *database* is not survivable and does raise.
    """
    if not snapshot.exists():
        raise BackupError("Database snapshot is missing — refusing to write an archive without it.")

    revision, counts, instance_id = read_snapshot_facts(snapshot)
    asset_files = list(_iter_asset_files(asset_root))
    skipped: list[str] = []
    asset_bytes = 0

    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zf:
        zf.write(snapshot, DB_ENTRY)

        for path in asset_files:
            relative = path.relative_to(asset_root).as_posix()
            try:
                asset_bytes += path.stat().st_size
                zf.write(path, f"{ASSETS_PREFIX}{relative}")
            except (FileNotFoundError, PermissionError, OSError) as exc:
                logger.warning("Skipping asset %s during backup: %s", relative, exc)
                skipped.append(relative)

        manifest = Manifest(
            format_version=FORMAT_VERSION,
            app_version=app_version,
            alembic_revision=revision,
            created_at=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            db_bytes=snapshot.stat().st_size,
            asset_file_count=len(asset_files) - len(skipped),
            asset_bytes=asset_bytes,
            counts=counts,
            # config.json is deliberately excluded, which means the Fernet key that decrypts
            # stored marketplace tokens does not travel with the archive. Consequence: restoring
            # onto different hardware needs Etsy/eBay reconnecting. Recorded as a flag rather
            # than assumed, so the restore UI can state the truth even if this changes.
            includes_config=False,
            skipped_assets=skipped,
            db_instance_id=instance_id,
        )
        zf.writestr(MANIFEST_NAME, json.dumps(manifest.to_dict(), indent=2))

    return manifest


def read_manifest(archive: Path) -> Manifest:
    """Read just the manifest out of an archive.

    Cheap regardless of archive size — zip keeps a central directory, so this seeks straight to
    the entry rather than streaming gigabytes of assets. That's what makes "list the backups" a
    directory glob instead of a database table.
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            return Manifest.from_dict(json.loads(zf.read(MANIFEST_NAME)))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError) as exc:
        raise BackupError(f"{archive.name} is not a readable StockSmith backup: {exc}") from exc
