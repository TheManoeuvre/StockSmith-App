"""The data behind GET /system/status.

Kept out of the router so the pieces are unit-testable without HTTP, and so the schema-revision
lookup has an obvious place to cache.
"""

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.general_settings import GeneralSettings

logger = logging.getLogger("stocksmith.system_status")

_cached_revision: str | None = None


async def alembic_revision(session: AsyncSession) -> str | None:
    """The schema revision this database is actually at.

    Read from `alembic_version` rather than from Alembic's `ScriptDirectory` head, because those
    are different questions: the script directory says what this *build* knows about, the table
    says what this *database* is. A restore needs the second one, and conflating them is how a
    downgrade check ends up passing on a database it shouldn't.

    Cached: it cannot change while the process is running. Migrations run in bootstrap, before
    the app serves anything, and a restore takes effect only across a restart.
    """
    global _cached_revision
    if _cached_revision is not None:
        return _cached_revision

    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        _cached_revision = result.scalar()
    except Exception:
        # A database built by `Base.metadata.create_all` (the test suite) has no alembic_version
        # table at all. Not knowing the revision is a real answer here, not an error.
        logger.debug("No alembic_version table; reporting an unknown schema revision")
        return None
    return _cached_revision


async def db_instance_id(session: AsyncSession) -> str | None:
    """This database lineage's id, generated on first read if it has none.

    Lazy rather than seeded so that databases predating the column — and databases arriving via a
    restored backup — both get handled by the same path. Note the value deliberately survives a
    restore *of that same database*: it identifies the lineage, and the fingerprint below pairs it
    with the restore timestamp to catch the case where an older backup of the same lineage is
    restored and the id is legitimately unchanged.
    """
    settings_row = (await session.execute(select(GeneralSettings).where(GeneralSettings.id == 1))).scalar_one_or_none()
    if settings_row is None:
        # Seeding hasn't run yet (or this is a bare test database). Nothing to attach an id to.
        return None
    if settings_row.db_instance_id is None:
        settings_row.db_instance_id = str(uuid.uuid4())
        await session.commit()
    return settings_row.db_instance_id


async def data_fingerprint(session: AsyncSession, last_restore_completed_at: str | None = None) -> str:
    """Changes whenever the database underneath a connected client has been swapped.

    Two components, because one isn't enough. The instance id catches restoring a *different*
    database; the restore timestamp catches restoring an *older backup of the same* database,
    where the id is unchanged but every row may not be.

    Clients treat a change as "throw the cache away", not "refetch" — see MaintenanceOverlay.
    """
    return f"{await db_instance_id(session) or ''}:{last_restore_completed_at or ''}"
