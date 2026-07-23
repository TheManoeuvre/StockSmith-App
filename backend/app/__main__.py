"""Packaged entrypoint for PyInstaller.

The frozen exe runs this directly (a plain Python script), not `uv run uvicorn
app.main:app` as in dev — bootstrap has to resolve the per-user data directory and set
DATABASE_URL/ASSET_ROOT/etc as environment variables *before* app.main (and the
app.config.Settings singleton it pulls in) is ever imported.

`multiprocessing.freeze_support()` is called first as a onedir/onefile safety net —
harmless if nothing in the process tree ever spawns a subprocess, but the standard
first line of any frozen entrypoint that might.
"""

import multiprocessing
import os


def main() -> None:
    multiprocessing.freeze_support()

    from app.bootstrap import run as bootstrap

    bootstrap()

    import uvicorn

    from app.main import app

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
