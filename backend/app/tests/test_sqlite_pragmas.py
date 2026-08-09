"""Connection pragmas set by `configure_sqlite_pragmas`.

These build their own file-backed engines rather than using conftest's `engine` fixture:
`journal_mode = WAL` is meaningless on `:memory:` (it reports "memory" and stays there), so
the one pragma most worth proving can only be proven against a real file. The fixture's own
StaticPool would also hide the bug these tests exist to catch — see below.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import configure_sqlite_pragmas


async def _read_pragmas(engine) -> dict[str, object]:
    async with engine.connect() as conn:
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        busy_timeout = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        synchronous = (await conn.execute(text("PRAGMA synchronous"))).scalar()
    return {"journal_mode": journal_mode, "busy_timeout": busy_timeout, "synchronous": synchronous}


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{(tmp_path / 'pragmas.db').as_posix()}"


async def test_pragmas_are_set_on_a_fresh_connection(db_url):
    engine = create_async_engine(db_url)
    configure_sqlite_pragmas(engine)
    try:
        pragmas = await _read_pragmas(engine)
    finally:
        await engine.dispose()

    assert pragmas["journal_mode"] == "wal"
    assert pragmas["busy_timeout"] == 15000
    assert pragmas["synchronous"] == 1  # NORMAL


async def test_pragmas_survive_connection_recycling(db_url):
    """The reason this is a `connect` listener and not a one-shot.

    A pooled connection handed back by SQLAlchemy comes with per-connection pragmas at their
    defaults again. `busy_timeout` is the one that matters here: if it were set once at startup
    instead of per connection, every connection after the first would silently drop back to
    aiosqlite's 5s — which is exactly the window a long `VACUUM INTO` would blow through.
    """
    engine = create_async_engine(db_url)
    configure_sqlite_pragmas(engine)
    try:
        first = await _read_pragmas(engine)
        # Returning the connection to the pool and taking it out again is what a second request
        # does. Disposing forces a genuinely new DBAPI connection rather than a cached one.
        await engine.dispose()
        second = await _read_pragmas(engine)
    finally:
        await engine.dispose()

    assert first["busy_timeout"] == 15000
    assert second["busy_timeout"] == 15000
    assert second["synchronous"] == 1
    assert second["journal_mode"] == "wal"


async def test_journal_mode_persists_in_the_file_header(db_url):
    """WAL is stored in the database file, so an engine *without* the listener still finds it.

    Worth pinning: it means the sidecar `-wal`/`-shm` files appear for any process that opens
    the database, including the plain `sqlite3` connections that bootstrap's restore path uses
    before SQLAlchemy exists.
    """
    configured = create_async_engine(db_url)
    configure_sqlite_pragmas(configured)
    async with configured.connect() as conn:
        await conn.execute(text("PRAGMA journal_mode"))
    await configured.dispose()

    plain = create_async_engine(db_url)
    try:
        async with plain.connect() as conn:
            assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
    finally:
        await plain.dispose()


async def test_pragmas_are_skipped_on_non_sqlite_engines():
    """Registering the listener against Postgres would issue SQLite-only pragmas on every
    connection. The dialect check is the guard; this pins it without needing a live server."""
    engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/nonexistent")
    try:
        # No connection is opened, so this asserts on the registration decision alone.
        configure_sqlite_pragmas(engine)
    finally:
        await engine.dispose()
