# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the packaged StockSmith backend.

Onefile, not onedir: onedir requires its companion `_internal/` folder to sit as a true
filesystem sibling of the exe, which Tauri's sidecar mechanism can't guarantee — the
`externalBin` convention copies only the one named file, and dev-mode testing confirmed
`resources` lands it in a different directory (`target/debug/binaries/_internal/`) than
the sidecar exe (`target/debug/stocksmith-backend.exe`), which breaks PyInstaller's
bootloader. Onefile self-extracts everything (DLLs, alembic.ini, alembic/versions/*.py)
into a per-run temp dir at startup, so there's no companion folder to keep adjacent at
all — `app.bootstrap._backend_root()` already resolves `sys._MEIPASS` uniformly, which
PyInstaller sets for onefile's temp-extraction dir the same way it does for onedir.

Build with:
    uv run pyinstaller stocksmith-backend.spec
Output lands at dist/stocksmith-backend.exe (single file).
"""

a = Analysis(
    ["app/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("alembic.ini", "."),
        ("alembic/env.py", "alembic"),
        ("alembic/script.py.mako", "alembic"),
        ("alembic/versions", "alembic/versions"),
    ],
    hiddenimports=[
        # Uvicorn resolves these by string name at runtime — PyInstaller's static
        # import scan can't see them.
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # DB driver + dialect used only via the DATABASE_URL string, never a direct
        # `import aiosqlite` anywhere in app code.
        "aiosqlite",
        "sqlalchemy.dialects.sqlite",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="stocksmith-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
