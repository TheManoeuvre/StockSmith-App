import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.routers import (
    assets,
    backups,
    builds,
    colours,
    dashboard,
    fee_config,
    manufacturers,
    material_types,
    materials,
    orders,
    platform_config,
    platforms,
    product_types,
    products,
    purchases,
    restore,
    shipping_profiles,
    stock_adjustments,
    stock_takes,
    suppliers,
    system,
    variants,
)
from app.services import backup_scheduler, maintenance, sync_scheduler

logger = logging.getLogger("stocksmith")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    sync_scheduler.start()
    backup_scheduler.start()
    try:
        yield
    finally:
        sync_scheduler.stop()
        backup_scheduler.stop()


app = FastAPI(title="StockSmith API", lifespan=lifespan)


class CatchUnhandledExceptionsMiddleware(BaseHTTPMiddleware):
    """Converts unhandled exceptions into a clean 500 JSON response.

    A FastAPI @app.exception_handler(Exception) would also do this, but Starlette always
    routes bare-Exception handlers through its outermost ServerErrorMiddleware, which sits
    outside CORSMiddleware — so the browser can't read the response and reports a generic
    "Failed to fetch" instead. Catching here, inside CORSMiddleware, keeps CORS headers on
    the response so the real error is visible to the client.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """Refuses ordinary API work while a restore is staged or being applied.

    Everything under /api/v1 gets a 503; /healthz, /system/status and the endpoint that cancels
    a staged restore stay reachable. That allowlist is the point: a client polling to find out
    when the outage ends must not be behind the wall, and the escape hatch that clears a staged
    restore must keep working while one is staged.

    Registration order below is load-bearing for the same reason the comment there gives —
    this must end up *inside* CORSMiddleware, or a thin client reads the 503 as an opaque
    "Failed to fetch" and can't tell a maintenance window from a dead backend.
    """

    _ALLOWED_EXACT = {"/healthz", "/system/status", "/api/v1/restore/pending"}

    async def dispatch(self, request: Request, call_next):
        phase = maintenance.current_phase()
        if phase is None or not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        if request.url.path in self._ALLOWED_EXACT:
            return await call_next(request)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": "StockSmith is restoring a backup. This will only take a moment.",
                "maintenance": True,
                "phase": phase,
            },
            headers={"Retry-After": "10"},
        )


# Registration order matters: Starlette wraps middleware in reverse of registration order
# (last added = outermost), so CORSMiddleware must be added after the exception-catching
# middleware to end up wrapping around it.
app.add_middleware(CatchUnhandledExceptionsMiddleware)
app.add_middleware(MaintenanceModeMiddleware)

# The real Tauri app talks to this API over the http plugin, which bypasses the webview's
# CORS restrictions entirely — this CORS config only matters for iterating against the
# frontend in a plain browser (`vite dev`) during development. Tailscale + the shared
# password are the actual access-control boundary, so a permissive CORS policy here is fine
# for this single-user LAN tool.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(materials.router, prefix="/api/v1")
app.include_router(material_types.router, prefix="/api/v1")
app.include_router(colours.router, prefix="/api/v1")
app.include_router(products.router, prefix="/api/v1")
app.include_router(product_types.router, prefix="/api/v1")
app.include_router(variants.router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(purchases.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(manufacturers.router, prefix="/api/v1")
app.include_router(suppliers.router, prefix="/api/v1")
app.include_router(builds.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(platforms.router, prefix="/api/v1")
app.include_router(fee_config.router, prefix="/api/v1")
app.include_router(platform_config.router, prefix="/api/v1")
app.include_router(shipping_profiles.router, prefix="/api/v1")
app.include_router(stock_adjustments.router, prefix="/api/v1")
app.include_router(stock_takes.router, prefix="/api/v1")
app.include_router(backups.router, prefix="/api/v1")
app.include_router(restore.router, prefix="/api/v1")

# Intentionally *not* under /api/v1: a client polling for the end of a restore has to reach this
# while everything under that prefix is answering 503. See app/routers/system.py.
app.include_router(system.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness, plus which build is answering.

    The version matters because the desktop shell probes this port on startup and reuses
    whatever responds. On Windows an installer cannot overwrite a running executable, so an
    update applied while the old backend is alive replaces the app but leaves the previous
    sidecar binary on disk — and the new shell then adopts a backend from the old release.
    Everything looks fine until the new UI calls an endpoint the old build has never heard
    of, which is exactly how 0.6.2 arrived with a working app and a broken Integrations
    page. Reporting the version is what lets the shell notice instead of guessing."""
    return {"status": "ok", "version": settings.app_version}


@app.get("/bootstrap-info")
async def bootstrap_info() -> dict[str, str]:
    """One-time, unauthenticated handoff of the auto-generated connection details to the
    Tauri app's first-run flow — deliberately not behind require_auth, since the whole
    point is handing over the password before the frontend has one to authenticate with.

    Only reachable in the packaged desktop app (app/bootstrap.py writes config.json under
    %LOCALAPPDATA%\\StockSmith\\ before this process starts serving) — plain `uv run
    uvicorn` dev instances have no such file and always 404 here. Consumed exactly once:
    after the first successful read, the config is flagged so this permanently 404s,
    rather than leaving a standing unauthenticated credential-read endpoint live.
    """
    from app.bootstrap import config_path

    path = config_path()
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("bootstrap_info_consumed"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    config["bootstrap_info_consumed"] = True
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return {"backendUrl": "http://127.0.0.1:8000", "sharedPassword": config["shared_password"]}
