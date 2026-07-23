import logging

from fastapi import FastAPI, Request
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
