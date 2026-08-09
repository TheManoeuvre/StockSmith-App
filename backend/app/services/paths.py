"""Where things live on disk.

Everything derives from `settings.asset_root`'s parent rather than re-deriving the data
directory the way bootstrap.py does. That's deliberate: bootstrap owns resolving
`%LOCALAPPDATA%\\StockSmith` and hands the result to the app as ASSET_ROOT, so reading it back
out means there is exactly one definition of where the data lives, and the `uv run uvicorn` dev
loop — which never calls bootstrap at all — works the same way with whatever ASSET_ROOT its
.env points at.
"""

from pathlib import Path
from urllib.parse import unquote, urlparse

from app.config import settings


class UnsupportedDatabaseError(RuntimeError):
    """Raised when an operation only defined for SQLite is attempted on another backend."""


def data_root() -> Path:
    return Path(settings.asset_root).parent


def asset_root() -> Path:
    return Path(settings.asset_root)


def backup_dir() -> Path:
    return data_root() / "backups"


def prerestore_dir() -> Path:
    """Snapshots taken automatically just before a restore overwrites everything.

    A sibling of the ordinary backups rather than one of them, because retention must not
    apply: a scheduled run trimming to the newest N could otherwise evict the exact rollback
    point someone needs.
    """
    return backup_dir() / "pre-restore"


def restore_dir() -> Path:
    return data_root() / "restore"


def sqlite_db_path() -> Path:
    """The database file behind `settings.database_url`.

    Raises UnsupportedDatabaseError for any non-SQLite URL. Callers are expected to check the
    dialect first and produce a useful message; this is the backstop that stops a Postgres
    install silently writing an empty or half-formed archive.
    """
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise UnsupportedDatabaseError(f"Not a SQLite database URL: {url.split('://')[0]}://…")

    # sqlite+aiosqlite:///C:/path/to.db  ->  C:/path/to.db
    # sqlite+aiosqlite:////abs/unix/path ->  /abs/unix/path
    path_part = urlparse(url).path
    if not path_part:
        raise UnsupportedDatabaseError(f"No file path in database URL: {url}")
    candidate = unquote(path_part).lstrip("/")
    # A POSIX absolute path loses its leading slash above; a Windows one ("C:/…") does not need
    # it back. Telling them apart by the drive-letter colon is crude but covers both platforms
    # this ever runs on.
    if not (len(candidate) > 1 and candidate[1] == ":"):
        candidate = "/" + candidate
    return Path(candidate)


def sqlite_sidecar_paths(db_path: Path) -> list[Path]:
    """The WAL and shared-memory files that travel with a WAL-mode database.

    Needed wherever a database file is swapped: a `-wal` left behind from a *different*
    database is applied to whatever now sits at that filename, which is straightforward
    corruption rather than a stale-read.
    """
    return [db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]


def ensure_dirs() -> None:
    """Create the backup/restore directories if they aren't there. Cheap and idempotent —
    called at service start rather than in bootstrap so the dev loop gets them too."""
    backup_dir().mkdir(parents=True, exist_ok=True)
    prerestore_dir().mkdir(parents=True, exist_ok=True)
    restore_dir().mkdir(parents=True, exist_ok=True)
