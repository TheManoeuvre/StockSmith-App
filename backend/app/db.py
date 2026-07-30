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


engine = create_async_engine(settings.database_url, echo=False)
enforce_sqlite_foreign_keys(engine)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
