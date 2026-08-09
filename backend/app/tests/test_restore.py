"""Restore: staging validation, and the boot-time swap.

The `maybe_apply_staged_restore` tests are the most valuable ones here. That function runs before
logging is useful, before an engine exists, and only on a real app restart — so it is close to
undebuggable live, and every one of its failure paths has to leave a bootable app.
"""

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.bootstrap import maybe_apply_staged_restore
from app.services import paths, restore
from app.services.backup_archive import build_archive, snapshot_database
from app.services.restore import RestoreError, RestoreVersionError


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "StockSmith"
    (root / "data").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    monkeypatch.setattr("app.config.settings.asset_root", str(root / "assets"))
    monkeypatch.setattr(
        "app.config.settings.database_url",
        f"sqlite+aiosqlite:///{(root / 'data' / 'stocksmith.db').as_posix()}",
    )
    return root


def _make_db(path: Path, names: list[str], revision: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE manufacturers (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO manufacturers (name) VALUES (?)", [(n,) for n in names])
    if revision:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))
    conn.commit()
    conn.close()
    return path


def _names_in(db: Path) -> set[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM manufacturers")}
    finally:
        conn.close()


def _make_archive(tmp_path: Path, root: Path, names: list[str], revision: str | None, assets: dict[str, bytes]):
    """A valid backup archive containing the given rows and asset files."""
    source_dir = tmp_path / f"src-{len(list(tmp_path.iterdir()))}"
    source_dir.mkdir()
    live = _make_db(source_dir / "live.db", names, revision)
    snapshot = source_dir / "snap.db"
    snapshot_database(live, snapshot)

    asset_root = source_dir / "assets"
    asset_root.mkdir()
    for relative, content in assets.items():
        target = asset_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    archive = source_dir / "backup.zip"
    build_archive(
        snapshot=snapshot, asset_root=asset_root, target_zip=archive, app_version="0.6.0", kind="manual"
    )
    return archive


class TestStaging:
    def test_stages_a_valid_backup(self, data_root, tmp_path, monkeypatch):
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        archive = _make_archive(tmp_path, data_root, ["Bambu Lab"], "abc123", {"products/a.jpg": b"img"})

        manifest = restore.stage(archive, source_filename="backup.zip", requested_from="127.0.0.1")

        assert manifest.alembic_revision == "abc123"
        marker = restore.read_pending()
        assert marker is not None and marker["state"] == "pending"
        assert (restore.staged_dir() / "db" / "stocksmith.db").exists()
        assert (restore.staged_dir() / "assets" / "products" / "a.jpg").read_bytes() == b"img"

    def test_refuses_a_backup_from_a_newer_build(self, data_root, tmp_path, monkeypatch):
        """Revision membership, not a version-string comparison — exact, and it can't be fooled
        by a version bumped without a migration."""
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        archive = _make_archive(tmp_path, data_root, ["Future"], "from-the-future", {})

        with pytest.raises(RestoreVersionError, match="newer version"):
            restore.stage(archive, source_filename="backup.zip", requested_from="127.0.0.1")

        assert restore.read_pending() is None
        assert not restore.staged_dir().exists()

    def test_accepts_a_backup_from_an_older_build(self, data_root, tmp_path, monkeypatch):
        """Bootstrap runs Alembic straight after the swap, so an older schema is fine."""
        monkeypatch.setattr(restore, "known_revisions", lambda: {"old-rev", "current-rev"})
        archive = _make_archive(tmp_path, data_root, ["Old"], "old-rev", {})

        restore.stage(archive, source_filename="backup.zip", requested_from="127.0.0.1")

        assert restore.read_pending() is not None

    def test_accepts_a_database_with_no_alembic_version(self, data_root, tmp_path, monkeypatch):
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        archive = _make_archive(tmp_path, data_root, ["Unversioned"], None, {})

        restore.stage(archive, source_filename="backup.zip", requested_from="127.0.0.1")

        assert restore.read_pending() is not None

    def test_refuses_a_corrupt_archive_and_leaves_the_live_data_alone(self, data_root, tmp_path):
        live = _make_db(data_root / "data" / "stocksmith.db", ["Untouched"])
        before = live.read_bytes()

        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"this is not a zip file")

        with pytest.raises(RestoreError):
            restore.stage(broken, source_filename="broken.zip", requested_from="127.0.0.1")

        assert live.read_bytes() == before
        assert restore.read_pending() is None

    def test_refuses_an_archive_whose_database_is_damaged(self, data_root, tmp_path, monkeypatch):
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        archive = tmp_path / "damaged.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("db/stocksmith.db", b"SQLite format 3\x00" + b"\x00" * 200)
            zf.writestr(
                "manifest.json",
                json.dumps({"format_version": 1, "app_version": "0.6.0", "db_bytes": 216, "asset_bytes": 0}),
            )

        with pytest.raises(RestoreError):
            restore.stage(archive, source_filename="damaged.zip", requested_from="127.0.0.1")

        assert restore.read_pending() is None

    def test_refuses_an_archive_containing_a_traversal_path(self, data_root, tmp_path, monkeypatch):
        """A zip can name a member anything, including a path that climbs out of the
        destination. Only recognised prefixes are extracted, and each is re-checked."""
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        good = _make_archive(tmp_path, data_root, ["Fine"], "abc123", {})

        poisoned = tmp_path / "poisoned.zip"
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(poisoned, "w") as dst:
            for item in src.infolist():
                dst.writestr(item, src.read(item.filename))
            dst.writestr("assets/../../../../evil.txt", b"pwned")

        with pytest.raises(RestoreError, match="unsafe path"):
            restore.stage(poisoned, source_filename="poisoned.zip", requested_from="127.0.0.1")

        assert not (data_root.parent.parent / "evil.txt").exists()

    def test_refuses_when_the_disk_is_too_full(self, data_root, tmp_path, monkeypatch):
        monkeypatch.setattr(restore, "known_revisions", lambda: {"abc123"})
        monkeypatch.setattr(restore, "_free_space_bytes", lambda _p: 1024)
        archive = _make_archive(tmp_path, data_root, ["Big"], "abc123", {"a.jpg": b"x" * 50_000})

        with pytest.raises(RestoreError, match="disk space"):
            restore.stage(archive, source_filename="backup.zip", requested_from="127.0.0.1")


class TestBootTimeApply:
    """The swap itself. Each test asserts the app is left bootable."""

    def _stage_manually(self, data_root: Path, names: list[str], assets: dict[str, bytes], revision=None) -> None:
        staged = data_root / "restore" / "staged"
        _make_db(staged / "db" / "stocksmith.db", names, revision)
        for relative, content in assets.items():
            target = staged / "assets" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (data_root / "restore").mkdir(parents=True, exist_ok=True)
        (data_root / "restore" / "RESTORE_PENDING.json").write_text(
            json.dumps(
                {
                    "state": "pending",
                    "attempts": 0,
                    "staged_dir": str(staged),
                    "source_filename": "backup.zip",
                    "manifest": {},
                }
            ),
            encoding="utf-8",
        )

    def test_swaps_the_database_and_assets(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Old data"])
        (data_root / "assets" / "old.jpg").write_bytes(b"old")
        self._stage_manually(data_root, ["Restored data"], {"new.jpg": b"new"})

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Restored data"}
        assert (data_root / "assets" / "new.jpg").read_bytes() == b"new"
        assert not (data_root / "assets" / "old.jpg").exists()

    def test_deletes_stale_wal_sidecars(self, data_root):
        """A WAL from the *old* database applied to the new file at that name is corruption,
        not a stale read. This is the single most dangerous thing the swap could get wrong."""
        _make_db(data_root / "data" / "stocksmith.db", ["Old"])
        (data_root / "data" / "stocksmith.db-wal").write_bytes(b"stale wal")
        (data_root / "data" / "stocksmith.db-shm").write_bytes(b"stale shm")
        self._stage_manually(data_root, ["New"], {})

        maybe_apply_staged_restore(data_root)

        assert not (data_root / "data" / "stocksmith.db-wal").exists()
        assert not (data_root / "data" / "stocksmith.db-shm").exists()
        assert _names_in(data_root / "data" / "stocksmith.db") == {"New"}

    def test_takes_a_pre_restore_snapshot(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Will be replaced"])
        self._stage_manually(data_root, ["Replacement"], {})

        maybe_apply_staged_restore(data_root)

        snapshots = list((data_root / "backups" / "pre-restore").glob("stocksmith-prerestore-*.zip"))
        assert len(snapshots) == 1
        with zipfile.ZipFile(snapshots[0]) as zf:
            extracted = zf.extract("db/stocksmith.db", data_root / "check")
        assert _names_in(Path(extracted)) == {"Will be replaced"}

    def test_records_success_and_clears_the_marker(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Old"])
        self._stage_manually(data_root, ["New"], {})

        maybe_apply_staged_restore(data_root)

        assert not (data_root / "restore" / "RESTORE_PENDING.json").exists()
        assert not (data_root / "restore" / "staged").exists()
        record = json.loads((data_root / "restore" / "last-restore.json").read_text())
        assert record["state"] == "done"
        assert record["acknowledged"] is False
        assert record["prerestore_filename"] is not None

    def test_rolls_back_when_the_swap_fails_partway(self, data_root, monkeypatch):
        """The property that matters most: a failed restore must never leave the user without a
        working app."""
        original = _make_db(data_root / "data" / "stocksmith.db", ["Precious"])
        (data_root / "assets" / "keep.jpg").write_bytes(b"keep")
        before_db = original.read_bytes()
        self._stage_manually(data_root, ["Replacement"], {"new.jpg": b"new"})

        import os as os_module

        real_replace = os_module.replace
        calls = {"n": 0}

        def fail_on_the_second_move(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:  # after the live db is moved aside, before the new one lands
                raise OSError("simulated failure mid-swap")
            return real_replace(src, dst)

        monkeypatch.setattr("app.bootstrap.os.replace", fail_on_the_second_move)

        maybe_apply_staged_restore(data_root)

        assert (data_root / "data" / "stocksmith.db").read_bytes() == before_db
        assert _names_in(data_root / "data" / "stocksmith.db") == {"Precious"}
        assert (data_root / "assets" / "keep.jpg").read_bytes() == b"keep"
        record = json.loads((data_root / "restore" / "last-restore.json").read_text())
        assert record["state"] == "failed"
        assert "unchanged" in record["error"]

    def test_waits_out_a_file_lock_instead_of_failing(self, data_root, monkeypatch):
        """Windows refuses to rename a file anything still has open, and the swap runs moments
        after the previous sidecar was killed — so a handle can outlive the process briefly.

        Found for real: a dry run against production data hit exactly this and marked the restore
        failed, for a condition that clears itself in milliseconds. The rollback was correct and
        lost nothing, but it burned one of only two attempts.
        """
        _make_db(data_root / "data" / "stocksmith.db", ["Original"])
        self._stage_manually(data_root, ["Restored"], {})

        import os as os_module

        real_replace = os_module.replace
        calls = {"n": 0}

        def locked_for_the_first_few_tries(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(32, "The process cannot access the file because it is being used")
            return real_replace(src, dst)

        monkeypatch.setattr("app.bootstrap.os.replace", locked_for_the_first_few_tries)
        # Keep the test quick — the production window is 15s of quarter-second retries.
        monkeypatch.setattr("app.bootstrap._MOVE_RETRY_SECONDS", 5.0)

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Restored"}
        record = json.loads((data_root / "restore" / "last-restore.json").read_text())
        assert record["state"] == "done"

    def test_a_lock_that_never_clears_still_rolls_back(self, data_root, monkeypatch):
        """The retry must not turn a permanent lock into a hang or, worse, a partial swap."""
        original = _make_db(data_root / "data" / "stocksmith.db", ["Precious"])
        before = original.read_bytes()
        self._stage_manually(data_root, ["Never applied"], {})

        def always_locked(src, dst):
            raise PermissionError(32, "The process cannot access the file because it is being used")

        monkeypatch.setattr("app.bootstrap.os.replace", always_locked)
        monkeypatch.setattr("app.bootstrap._MOVE_RETRY_SECONDS", 0.5)

        maybe_apply_staged_restore(data_root)

        assert (data_root / "data" / "stocksmith.db").read_bytes() == before
        record = json.loads((data_root / "restore" / "last-restore.json").read_text())
        assert record["state"] == "failed"

    def test_gives_up_after_two_attempts_rather_than_looping(self, data_root):
        """A restore that hard-crashes the process must not brick the app into a reboot loop."""
        _make_db(data_root / "data" / "stocksmith.db", ["Original"])
        self._stage_manually(data_root, ["Never applied"], {})
        marker_path = data_root / "restore" / "RESTORE_PENDING.json"
        marker = json.loads(marker_path.read_text())
        marker["attempts"] = 2
        marker_path.write_text(json.dumps(marker))

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Original"}
        assert not marker_path.exists()
        record = json.loads((data_root / "restore" / "last-restore.json").read_text())
        assert record["state"] == "failed"

    def test_does_nothing_without_a_marker(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Untouched"])

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Untouched"}
        assert not (data_root / "restore" / "last-restore.json").exists()

    def test_ignores_an_unreadable_marker_and_boots_normally(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Untouched"])
        (data_root / "restore").mkdir(parents=True, exist_ok=True)
        (data_root / "restore" / "RESTORE_PENDING.json").write_text("{ not json")

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Untouched"}
        assert not (data_root / "restore" / "RESTORE_PENDING.json").exists()

    def test_abandons_a_marker_whose_staged_database_is_missing(self, data_root):
        _make_db(data_root / "data" / "stocksmith.db", ["Untouched"])
        (data_root / "restore").mkdir(parents=True, exist_ok=True)
        (data_root / "restore" / "RESTORE_PENDING.json").write_text(
            json.dumps({"state": "pending", "attempts": 0, "staged_dir": str(data_root / "restore" / "staged")})
        )

        maybe_apply_staged_restore(data_root)

        assert _names_in(data_root / "data" / "stocksmith.db") == {"Untouched"}
        assert not (data_root / "restore" / "RESTORE_PENDING.json").exists()

    def test_a_backup_with_no_assets_leaves_an_empty_directory(self, data_root):
        """Legitimate for a brand-new shop. The app expects the directory to exist."""
        _make_db(data_root / "data" / "stocksmith.db", ["Old"])
        (data_root / "assets" / "old.jpg").write_bytes(b"old")
        self._stage_manually(data_root, ["New"], {})
        # Remove the staged assets dir entirely, as an assetless archive would produce.
        import shutil as shutil_module

        shutil_module.rmtree(data_root / "restore" / "staged" / "assets", ignore_errors=True)

        maybe_apply_staged_restore(data_root)

        assert (data_root / "assets").is_dir()
        assert list((data_root / "assets").iterdir()) == []


class TestLastRestoreRecord:
    def test_acknowledge_keeps_the_record_but_drops_the_notification(self, data_root):
        paths.ensure_dirs()
        restore.last_restore_path().write_text(
            json.dumps(
                {
                    "state": "done",
                    "completed_at": "2026-08-09T12:00:00Z",
                    "error": None,
                    "source_filename": "backup.zip",
                    "prerestore_filename": "pre.zip",
                    "acknowledged": False,
                }
            )
        )

        restore.acknowledge_last_restore()

        record = restore.read_last_restore()
        assert record is not None
        assert record.acknowledged is True
        assert record.source_filename == "backup.zip"
