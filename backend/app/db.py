from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def enforce_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Issue `PRAGMA foreign_keys = ON` on every new connection this engine opens.

    SQLite ships with foreign-key enforcement *off*, and the setting is per-connection
    rather than stored in the database file — so without this, every `ON DELETE CASCADE`
    and `ON DELETE SET NULL` in the schema is inert declaration. Not a theoretical
    concern: `DELETE /orders/{id}` deletes the order row and relies on database-level
    cascade for `allocation_events`, `order_line_returns`, `order_kitting_allocations`
    and `order_kitting_overrides` — none of which has an ORM relationship to hang
    `cascade="all, delete-orphan"` off, the way `Order.lines` does — so every order
    deleted through the UI silently orphaned its audit rows.

    Registered as a `connect` listener rather than executed once, because a pooled
    connection recycled by SQLAlchemy comes back with the pragma at its default again.

    No-op on Postgres (the other supported `DATABASE_URL` backend), which enforces
    foreign keys unconditionally and has no such pragma — hence the dialect check
    instead of registering the listener blindly.

    Deliberately not applied to the engine Alembic builds in `alembic/env.py`: that is a
    separate engine by design, and SQLite migrations that rebuild a table have to be free
    to move rows while references are momentarily dangling.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_foreign_keys_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()


def configure_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Put SQLite into WAL mode with a generous busy timeout, on every new connection.

    Kept separate from `enforce_sqlite_foreign_keys` so that function stays a single-purpose
    thing test_foreign_key_enforcement.py can toggle to compare enforced against unenforced
    behaviour.

    WAL is a prerequisite for backups, not a general performance tweak. `VACUUM INTO` — the
    only safe way to snapshot a live SQLite database — holds a SHARED read lock for its whole
    duration. Under the default rollback journal a reader blocks writers, so a snapshot of any
    size would hand `SQLITE_BUSY` to whatever `sync_scheduler` or `listing_push` happened to be
    committing at the time. In WAL, readers and writers don't block each other at all.

    `busy_timeout` raises aiosqlite's 5s default, which is short for a desktop app that can be
    mid-marketplace-sync when the user does something interactive. `synchronous = NORMAL` is the
    standard companion to WAL: durable across process crashes, and only at risk from an OS-level
    crash or power loss, which is what the backups are for.

    Two consequences to carry forward, both load-bearing for backup/restore:
      - WAL adds `-wal` and `-shm` sidecar files. Copying `stocksmith.db` on its own is now
        definitely lossy, which is precisely why backups must go through `VACUUM INTO`.
      - Restoring a database file means deleting any stale `-wal`/`-shm` first. A WAL left over
        from a different database is straightforward corruption.

    `journal_mode` persists in the file header, so the first connection settles it for good; the
    others are per-connection and have to be reissued, hence a `connect` listener rather than a
    one-shot. No-op on Postgres, which has none of these pragmas.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 15000")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()


engine = create_async_engine(settings.database_url, echo=False)
enforce_sqlite_foreign_keys(engine)
configure_sqlite_pragmas(engine)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
