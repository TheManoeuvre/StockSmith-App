import json
import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.routers import (
    assets,
    builds,
    dashboard,
    fee_config,
    manufacturers,
    material_types,
    materials,
    orders,
    platforms,
    products,
    purchases,
    shipping_profiles,
    stock_adjustments,
    suppliers,
    variants,
)

logger = logging.getLogger("stocksmith")

app = FastAPI(title="StockSmith API")


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


# Registration order matters: Starlette wraps middleware in reverse of registration order
# (last added = outermost), so CORSMiddleware must be added after the exception-catching
# middleware to end up wrapping around it.
app.add_middleware(CatchUnhandledExceptionsMiddleware)

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
app.include_router(products.router, prefix="/api/v1")
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
app.include_router(shipping_profiles.router, prefix="/api/v1")
app.include_router(stock_adjustments.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


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
