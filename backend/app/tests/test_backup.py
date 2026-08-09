"""Backup archive construction, retention and filename safety.

These build their own file-backed engines rather than using conftest's `engine` fixture: the
whole feature is about a database that exists as a file, and `:memory:` can neither be
VACUUM INTO'd usefully nor exercise WAL.
"""

import asyncio
import sqlite3
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.db import configure_sqlite_pragmas, enforce_sqlite_foreign_keys
from app.models.base import Base
from app.models.manufacturer import Manufacturer
from app.services import backup, paths
from app.services.backup_archive import (
    ASSETS_PREFIX,
    DB_ENTRY,
    MANIFEST_NAME,
    BackupError,
    build_archive,
    read_manifest,
    snapshot_database,
)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """A fake %LOCALAPPDATA%\\StockSmith, with the app pointed at it.

    paths.py derives everything from asset_root's parent, so setting that one value relocates
    the database, assets, backups and restore staging together.
    """
    root = tmp_path / "StockSmith"
    (root / "data").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    monkeypatch.setattr("app.config.settings.asset_root", str(root / "assets"))
    monkeypatch.setattr("app.config.settings.app_version", "9.9.9")
    return root


@pytest_asyncio.fixture
async def file_engine(data_root, monkeypatch):
    db_path = data_root / "data" / "stocksmith.db"
    monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    enforce_sqlite_foreign_keys(engine)
    configure_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # The service reaches for the module-level engine rather than taking one, so redirect it —
    # the same trick conftest uses for order_sync's session factory.
    monkeypatch.setattr(backup, "engine", engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(file_engine):
    factory = async_sessionmaker(file_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


def _write_asset(data_root: Path, relative: str, content: bytes = b"x") -> Path:
    path = data_root / "assets" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestArchiveRoundTrip:
    async def test_backup_contains_the_rows_that_were_committed(self, session, data_root):
        session.add(Manufacturer(name="Bambu Lab"))
        session.add(Manufacturer(name="Prusa"))
        await session.commit()

        result = await backup.run_backup(session)

        archive = data_root / "backups" / result.filename
        assert archive.exists()
        with zipfile.ZipFile(archive) as zf:
            extracted = zf.extract(DB_ENTRY, data_root / "check")
        names = {row[0] for row in sqlite3.connect(extracted).execute("SELECT name FROM manufacturers")}
        assert names == {"Bambu Lab", "Prusa"}

    async def test_a_plain_file_copy_of_the_live_db_is_useless(self, session, data_root):
        """The reason VACUUM INTO is mandatory rather than merely preferred.

        In WAL mode, committed data lives in the `-wal` sidecar until a checkpoint folds it back
        into the main file. Copying `stocksmith.db` on its own therefore captures whatever was
        last checkpointed — which on a young database is nothing at all: the copy below doesn't
        just lose recent rows, it has no tables, because even the schema is still in the WAL.

        Pinned as a test because "copy the .db file" is the obvious-looking shortcut, and the
        failure mode is a backup that looks fine on disk and turns out to be empty at exactly
        the moment someone needs it.
        """
        session.add(Manufacturer(name="Written just now"))
        await session.commit()

        live_db = data_root / "data" / "stocksmith.db"
        assert live_db.with_name(live_db.name + "-wal").exists(), "expected WAL mode to be active"

        naive_copy = data_root / "naive.db"
        naive_copy.write_bytes(live_db.read_bytes())
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            sqlite3.connect(naive_copy).execute("SELECT name FROM manufacturers").fetchall()

        result = await backup.run_backup(session)
        with zipfile.ZipFile(data_root / "backups" / result.filename) as zf:
            extracted = zf.extract(DB_ENTRY, data_root / "check")

        assert {row[0] for row in sqlite3.connect(extracted).execute("SELECT name FROM manufacturers")} == {
            "Written just now"
        }

    async def test_assets_are_included_with_their_tree_intact(self, session, data_root):
        _write_asset(data_root, "products/0001-thing/images/main.jpg", b"jpeg-bytes")
        _write_asset(data_root, "materials/0002-filament/main.png", b"png-bytes")

        result = await backup.run_backup(session)

        with zipfile.ZipFile(data_root / "backups" / result.filename) as zf:
            names = set(zf.namelist())
            assert f"{ASSETS_PREFIX}products/0001-thing/images/main.jpg" in names
            assert f"{ASSETS_PREFIX}materials/0002-filament/main.png" in names
            assert zf.read(f"{ASSETS_PREFIX}materials/0002-filament/main.png") == b"png-bytes"
        assert result.manifest.asset_file_count == 2

    async def test_manifest_describes_the_snapshot(self, session, data_root):
        session.add(Manufacturer(name="One"))
        await session.commit()
        _write_asset(data_root, "products/0001-x/images/a.jpg")

        result = await backup.run_backup(session)
        manifest = read_manifest(data_root / "backups" / result.filename)

        assert manifest.app_version == "9.9.9"
        assert manifest.kind == "manual"
        assert manifest.asset_file_count == 1
        assert manifest.db_bytes > 0
        # False is load-bearing: the restore UI drives its "reconnect Etsy/eBay" warning off this
        # rather than a hard-coded string, so it stays honest if the decision ever changes.
        assert manifest.includes_config is False

    async def test_manifest_revision_comes_from_the_snapshot(self, session, data_root):
        """Not from Alembic's script directory — those answer different questions, and only the
        database's own answer makes a restore downgrade-check sound."""
        await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('c4e8f21a7b93')"))
        await session.commit()

        result = await backup.run_backup(session)

        assert read_manifest(data_root / "backups" / result.filename).alembic_revision == "c4e8f21a7b93"


class TestConcurrency:
    async def test_a_write_can_land_while_a_snapshot_is_running(self, session, file_engine, data_root):
        """What WAL bought. Under the default rollback journal the snapshot's read lock would
        block this write until it timed out, so any backup of real size would reliably break
        whatever sync_scheduler was committing at the time.
        """
        factory = async_sessionmaker(file_engine, expire_on_commit=False)

        async def write_during_backup():
            await asyncio.sleep(0)
            async with factory() as other:
                other.add(Manufacturer(name="Written mid-backup"))
                await other.commit()

        _, result = await asyncio.gather(write_during_backup(), backup.run_backup(session))

        assert (data_root / "backups" / result.filename).exists()
        names = {row[0] for row in (await session.execute(text("SELECT name FROM manufacturers")))}
        assert "Written mid-backup" in names


class TestRetention:
    def _fake_archive(self, directory: Path, stamp: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"stocksmith-backup-{stamp}.zip"
        path.write_bytes(b"not-a-real-zip")
        return path

    async def test_keeps_the_newest_n_and_deletes_oldest_first(self, data_root):
        directory = paths.backup_dir()
        for stamp in ("20260801-000000", "20260802-000000", "20260803-000000", "20260804-000000"):
            self._fake_archive(directory, stamp)

        backup._prune(directory, keep=2)

        remaining = sorted(p.name for p in directory.glob("*.zip"))
        assert remaining == ["stocksmith-backup-20260803-000000.zip", "stocksmith-backup-20260804-000000.zip"]

    async def test_pre_restore_snapshots_are_not_counted_against_retention(self, session, data_root):
        """A scheduled run must never evict the rollback point for a restore that just happened."""
        prerestore = paths.prerestore_dir()
        prerestore.mkdir(parents=True, exist_ok=True)
        keeper = prerestore / "stocksmith-prerestore-20260801-000000.zip"
        keeper.write_bytes(b"precious")

        for _ in range(3):
            await backup.run_backup(session)

        assert keeper.exists()

    async def test_only_our_own_files_are_ever_deleted(self, data_root, tmp_path):
        """The secondary directory is the user's — very likely a general-purpose OneDrive folder.
        Pruning there must not touch anything we didn't write."""
        secondary = tmp_path / "OneDrive"
        secondary.mkdir()
        (secondary / "notes.txt").write_text("do not delete me")
        (secondary / "holiday.jpg").write_bytes(b"photo")
        for stamp in ("20260801-000000", "20260802-000000"):
            self._fake_archive(secondary, stamp)

        backup._prune(secondary, keep=1)

        assert (secondary / "notes.txt").exists()
        assert (secondary / "holiday.jpg").exists()
        assert len(list(secondary.glob("stocksmith-backup-*.zip"))) == 1


class TestSecondaryDirectory:
    async def test_backup_is_copied_to_the_secondary_directory(self, session, data_root, tmp_path):
        secondary = tmp_path / "OneDrive"
        secondary.mkdir()
        config = await backup.get_settings_row(session)
        config.secondary_dir = str(secondary)
        await session.commit()

        result = await backup.run_backup(session)

        assert (secondary / result.filename).exists()
        assert config.secondary_dir_last_error is None
        assert config.secondary_dir_last_ok_at is not None

    async def test_a_broken_secondary_records_the_error_without_failing_the_backup(
        self, session, data_root, tmp_path
    ):
        """A OneDrive folder that stopped existing must be visible, not silent — but the primary
        copy is real and the run should still count as a success."""
        config = await backup.get_settings_row(session)
        config.secondary_dir = str(tmp_path / "gone" / "missing" / "\x00invalid")
        await session.commit()

        result = await backup.run_backup(session)

        assert (data_root / "backups" / result.filename).exists()
        assert config.last_run_status == "ok"
        assert config.secondary_dir_last_error is not None


class TestFilenameSafety:
    @pytest.mark.parametrize(
        "candidate",
        [
            "../../config.json",
            "..\\config.json",
            "stocksmith-backup-20260801-000000.zip/../../../secrets",
            "/etc/passwd",
            "C:/Windows/System32/config",
            "config.json",
            "stocksmith-backup.zip",
            "stocksmith-backup-2026-08-01.zip",
        ],
    )
    def test_rejects_anything_that_is_not_one_of_our_own_names(self, data_root, candidate):
        with pytest.raises(BackupError):
            backup.resolve_archive(candidate)

    def test_rejects_a_well_formed_name_that_does_not_exist(self, data_root):
        paths.ensure_dirs()
        with pytest.raises(BackupError):
            backup.resolve_archive("stocksmith-backup-20260801-000000.zip")

    async def test_resolves_a_real_archive(self, session, data_root):
        result = await backup.run_backup(session)
        assert backup.resolve_archive(result.filename).name == result.filename


class TestUnsupportedBackend:
    async def test_postgres_is_refused_and_writes_nothing(self, session, data_root, monkeypatch):
        """Never emit an archive with no database in it. The router turns this into a 501 and the
        UI renders an explanation instead of controls."""

        class FakeDialect:
            name = "postgresql"

        monkeypatch.setattr(backup.engine, "dialect", FakeDialect())

        assert backup.is_supported() is False
        with pytest.raises(backup.BackupUnsupportedError):
            await backup.run_backup(session)
        assert list(paths.backup_dir().glob("*.zip")) == []


class TestPartialFailures:
    def test_an_asset_deleted_mid_run_is_skipped_rather_than_fatal(self, data_root, tmp_path):
        """Realistic: a shop icon is rewritten while the zip is being built. One missing
        thumbnail must not cost you the whole archive."""
        db = data_root / "data" / "stocksmith.db"
        sqlite3.connect(str(db)).close()
        snapshot = tmp_path / "snap.db"
        snapshot_database(db, snapshot)

        kept = _write_asset(data_root, "products/0001-x/images/kept.jpg", b"kept")
        doomed = _write_asset(data_root, "products/0001-x/images/doomed.jpg", b"gone")

        real_write = zipfile.ZipFile.write

        def write_but_lose_one(self, filename, arcname=None, *args, **kwargs):
            if Path(filename) == doomed:
                doomed.unlink()
            return real_write(self, filename, arcname, *args, **kwargs)

        target = tmp_path / "out.zip"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(zipfile.ZipFile, "write", write_but_lose_one)
            manifest = build_archive(
                snapshot=snapshot,
                asset_root=data_root / "assets",
                target_zip=target,
                app_version="9.9.9",
                kind="manual",
            )

        assert manifest.skipped_assets == ["products/0001-x/images/doomed.jpg"]
        with zipfile.ZipFile(target) as zf:
            assert f"{ASSETS_PREFIX}products/0001-x/images/kept.jpg" in zf.namelist()
            assert MANIFEST_NAME in zf.namelist()
        assert kept.exists()

    def test_a_missing_snapshot_is_fatal(self, data_root, tmp_path):
        with pytest.raises(BackupError):
            build_archive(
                snapshot=tmp_path / "nope.db",
                asset_root=data_root / "assets",
                target_zip=tmp_path / "out.zip",
                app_version="9.9.9",
                kind="manual",
            )

    def test_snapshot_refuses_to_overwrite_an_existing_target(self, data_root, tmp_path):
        """So a half-written previous attempt can never be mistaken for a complete one."""
        db = data_root / "data" / "stocksmith.db"
        sqlite3.connect(str(db)).close()
        target = tmp_path / "snap.db"
        target.write_bytes(b"leftover")

        with pytest.raises(BackupError):
            snapshot_database(db, target)


class TestScheduling:
    @pytest.mark.parametrize(
        "hour,last_run_iso,expected",
        [
            (3, None, True),  # never run
            (3, "2026-08-09T03:30:00", False),  # already ran after today's hour
            (3, "2026-08-08T03:30:00", True),  # last ran yesterday
            (3, "2026-08-09T01:00:00", True),  # ran today but before the hour came round
        ],
    )
    def test_due_calculation(self, hour, last_run_iso, expected):
        from datetime import datetime as dt

        from app.services.backup_scheduler import _is_due

        now = dt(2026, 8, 9, 4, 0, 0)
        last_run = dt.fromisoformat(last_run_iso).astimezone() if last_run_iso else None
        assert _is_due(now, hour, last_run) is expected

    def test_not_due_before_the_scheduled_hour(self):
        from datetime import datetime as dt

        from app.services.backup_scheduler import _is_due

        assert _is_due(dt(2026, 8, 9, 2, 0, 0), 3, None) is False
