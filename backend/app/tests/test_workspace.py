"""The sample workspace switch: start-up opens the right database, builds the sample once, leaves
the real database alone, and tidies the sample away on the way out."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import bootstrap
from app.routers import workspace as workspace_router

_BACKEND = Path(__file__).resolve().parent.parent.parent


def _start(local_app_data: Path, then: str = "") -> str:
    """Run the packaged app's start-up in a fresh process, as a launch would, against a
    throwaway data folder. Returns the database it chose."""
    code = (
        "import os\n"
        "from app import bootstrap\n"
        f"{then}\n"
        "bootstrap.run()\n"
        "print(os.environ['DATABASE_URL'])\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DATABASE_URL", "ASSET_ROOT", "SHARED_PASSWORD_HASH", "TOKEN_ENCRYPTION_KEY"}
    }
    env["LOCALAPPDATA"] = str(local_app_data)
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=300
    )
    assert out.returncode == 0, out.stderr[-3000:]
    return out.stdout.strip().splitlines()[-1]


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_entering_and_leaving_the_sample(tmp_path):
    root = tmp_path / "StockSmith"
    real_db = root / "data" / "stocksmith.db"
    sample_db = root / "sample" / "data" / "stocksmith.db"

    # A normal first launch opens the real, empty database.
    assert _start(tmp_path).endswith("StockSmith/data/stocksmith.db")
    assert _count(real_db, "products") == 0

    # Entering the sample builds it in its own folder; the real database is untouched.
    assert _start(tmp_path, "bootstrap.request_workspace('sample')").endswith("sample/data/stocksmith.db")
    assert _count(sample_db, "products") == 3
    assert _count(real_db, "products") == 0

    # A second start in the sample reuses it rather than building another.
    _start(tmp_path)
    assert _count(sample_db, "orders") == 23

    # Leaving opens the real database and deletes the sample.
    assert _start(tmp_path, "bootstrap.request_workspace('real')").endswith("StockSmith/data/stocksmith.db")
    assert not (root / "sample").exists()
    assert not (root / bootstrap.WORKSPACE_MARKER).exists()


class TestApi:
    async def test_reports_the_current_workspace(self, monkeypatch):
        monkeypatch.setenv(bootstrap.WORKSPACE_ENV, "sample")
        state = await workspace_router.get_workspace()
        assert (state.mode, state.can_switch) == ("sample", True)

    async def test_switching_writes_a_request_for_the_next_start(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setenv(bootstrap.WORKSPACE_ENV, "real")
        await workspace_router.switch_workspace(workspace_router.WorkspaceSwitch(mode="sample"))
        assert (tmp_path / "StockSmith" / bootstrap.WORKSPACE_MARKER).read_text().count('"sample"') == 1

    async def test_refuses_without_the_desktop_start_up(self, monkeypatch):
        monkeypatch.delenv(bootstrap.WORKSPACE_ENV, raising=False)
        with pytest.raises(HTTPException) as exc:
            await workspace_router.switch_workspace(workspace_router.WorkspaceSwitch(mode="sample"))
        assert exc.value.status_code == 409

    async def test_restore_is_refused_inside_the_sample(self, monkeypatch):
        from app.routers import restore

        monkeypatch.setenv(bootstrap.WORKSPACE_ENV, "sample")
        with pytest.raises(HTTPException) as exc:
            await restore.stage_existing_backup(restore.StageByFilename(filename="x.zip"))
        assert exc.value.status_code == 409
