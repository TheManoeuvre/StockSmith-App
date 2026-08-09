"""The facts behind /system/status.

Tested at the service layer, matching the rest of the suite — the router is a five-line wrapper
and the interesting behaviour (lazy id generation, fingerprint composition, the missing-table
fallback) is all here.
"""

import pytest
from sqlalchemy import select, text

from app.models.general_settings import CurrencyCode, GeneralSettings
from app.services import maintenance, system_status


@pytest.fixture(autouse=True)
def clear_revision_cache():
    """`alembic_revision` memoises into a module global, which would otherwise leak between
    tests in whatever order they happen to run."""
    system_status._cached_revision = None
    yield
    system_status._cached_revision = None


@pytest.fixture(autouse=True)
def clear_maintenance_state():
    maintenance.exit()
    yield
    maintenance.exit()


async def _seed_settings(session) -> GeneralSettings:
    row = GeneralSettings(id=1, default_currency=CurrencyCode.GBP)
    session.add(row)
    await session.commit()
    return row


async def test_db_instance_id_is_generated_on_first_read(session):
    row = await _seed_settings(session)
    assert row.db_instance_id is None

    generated = await system_status.db_instance_id(session)

    assert generated is not None
    assert len(generated) == 36  # uuid4 string form
    stored = (await session.execute(select(GeneralSettings.db_instance_id))).scalar_one()
    assert stored == generated


async def test_db_instance_id_is_stable_across_reads(session):
    """It identifies a database lineage, so it must not churn — a client comparing fingerprints
    would read a new id as "the database was replaced" and throw its cache away every poll."""
    await _seed_settings(session)

    first = await system_status.db_instance_id(session)
    second = await system_status.db_instance_id(session)

    assert first == second


async def test_db_instance_id_is_none_before_seeding(session):
    """No settings row yet (first boot, mid-bootstrap). Reporting no id beats creating a
    half-populated settings row as a side effect of a status probe."""
    assert await system_status.db_instance_id(session) is None


async def test_fingerprint_changes_when_a_restore_completes(session):
    """The case the instance id alone cannot catch: an older backup of the *same* database is
    restored, so the lineage id is legitimately unchanged but the rows are not."""
    await _seed_settings(session)

    before = await system_status.data_fingerprint(session)
    after = await system_status.data_fingerprint(session, last_restore_completed_at="2026-08-09T10:00:00Z")

    assert before != after
    assert after.endswith(":2026-08-09T10:00:00Z")


async def test_fingerprint_changes_when_the_lineage_changes(session):
    """The other half: a backup from a different database entirely."""
    row = await _seed_settings(session)
    before = await system_status.data_fingerprint(session)

    row.db_instance_id = "11111111-1111-4111-8111-111111111111"
    await session.commit()

    assert await system_status.data_fingerprint(session) != before


async def test_alembic_revision_is_none_without_the_version_table(session):
    """conftest builds the schema with `create_all`, so there is no alembic_version table. An
    unknown revision is a real answer, not a 500 — the endpoint has to keep working on a database
    that Alembic has never touched."""
    assert await system_status.alembic_revision(session) is None


async def test_alembic_revision_reads_the_database_not_the_script_directory(session):
    """The distinction that makes the restore downgrade-check correct: this reports what the
    database *is* at, not what this build's migration scripts know about."""
    await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    await session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('a1b7c3d92e50')"))
    await session.commit()

    assert await system_status.alembic_revision(session) == "a1b7c3d92e50"


async def test_alembic_revision_is_cached(session):
    """It cannot change while the process runs — migrations happen in bootstrap, and a restore
    only takes effect across a restart — so re-reading it on every poll is pure waste."""
    await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    await session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('a1b7c3d92e50')"))
    await session.commit()

    assert await system_status.alembic_revision(session) == "a1b7c3d92e50"

    await session.execute(text("UPDATE alembic_version SET version_num = 'something-else'"))
    await session.commit()

    assert await system_status.alembic_revision(session) == "a1b7c3d92e50"


def test_maintenance_starts_inactive():
    assert maintenance.current_phase() is None
    assert maintenance.is_active() is False


def test_maintenance_reports_its_phase():
    maintenance.enter("restore_staged")
    assert maintenance.is_active() is True
    assert maintenance.current_phase() == "restore_staged"

    maintenance.exit()
    assert maintenance.is_active() is False
    assert maintenance.current_phase() is None
