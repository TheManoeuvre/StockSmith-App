import base64
import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db, require_auth
from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_sync_run import PlatformSyncRun
from app.schemas.listing import BulkListingSyncResult, ProductListingSyncSummary
from app.schemas.platform import (
    PlatformConnectResponse,
    PlatformStatus,
    SyncCommitResult,
    SyncPreviewResult,
    SyncRunPage,
    SyncStartDateUpdate,
)
from app.services import listing_sync, order_sync
from app.services.file_storage import resolve_asset_path, save_platform_icon
from app.services.platforms import get_adapter
from app.services.platforms.errors import PlatformAuthError, PlatformError, PlatformRateLimitError, PlatformSyncError
from app.services.url_import import fetch_image_bytes

logger = logging.getLogger("stocksmith.platforms")

router = APIRouter(prefix="/platforms", tags=["platforms"])

# Display labels for the backend-rendered OAuth callback page — the frontend has its
# own PLATFORM_LABELS (frontend/src/lib/platforms.ts) for everything rendered through
# React; this is the one place the backend itself renders a platform name directly.
_PLATFORM_LABELS: dict[ListingPlatform, str] = {
    ListingPlatform.etsy: "Etsy",
    ListingPlatform.ebay: "eBay",
    ListingPlatform.shopify: "Shopify",
}

# OAuth scopes to request per platform — kept here (not on the adapter) since scope
# selection is a StockSmith policy decision (which capabilities we actually use), not
# an intrinsic property of the marketplace's API. listings_w/sell.inventory (write)
# are intentionally omitted for both — nothing writes listings yet (push_listing_quantity
# is still a NotImplementedError stub on every adapter); re-add write scopes once that lands.
_SCOPES: dict[ListingPlatform, list[str]] = {
    ListingPlatform.etsy: ["listings_r", "transactions_r"],
    ListingPlatform.ebay: [
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
        "https://api.ebay.com/oauth/api_scope/sell.finances",
        "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
    ],
}

# In-memory PKCE verifier/state store for the brief connect -> callback round trip. A
# module-level dict is sufficient here: this is a single-process desktop-app backend
# (no multi-instance deployment), and entries are short-lived — the user completes the
# marketplace's consent screen within a few minutes or the entry expires and connect
# must be retried. Non-PKCE adapters (eBay) simply never read the stored verifier back
# out — see EbayAdapter's own docstring.
_PENDING: dict[str, tuple[str, float]] = {}
_PENDING_TTL_SECONDS = 600


def _cleanup_pending() -> None:
    cutoff = time.time() - _PENDING_TTL_SECONDS
    for s in [s for s, (_, created_at) in _PENDING.items() if created_at < cutoff]:
        _PENDING.pop(s, None)


def _redirect_uri(platform: ListingPlatform) -> str:
    """The value passed as OAuth `redirect_uri` — for URL-based platforms (Etsy) this
    is a literal callback URL; for eBay it's the opaque RuName eBay assigns to a
    redirect configuration registered in its dev portal (see config.py's ebay_ru_name
    docstring) — the real callback URL is entered there once, not built dynamically."""
    if platform == ListingPlatform.ebay:
        if not settings.ebay_ru_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ebay_ru_name is not configured")
        return settings.ebay_ru_name
    if not settings.public_base_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="public_base_url is not configured")
    return f"{settings.public_base_url.rstrip('/')}/api/v1/platforms/{platform.value}/callback"


async def _get_or_create_connection(session: AsyncSession, platform: ListingPlatform) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = PlatformConnection(platform=platform)
        session.add(connection)
    return connection


async def _require_connection(session: AsyncSession, platform: ListingPlatform) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None or not connection.is_connected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{_PLATFORM_LABELS[platform]} is not connected")
    return connection


def _map_platform_error(e: PlatformError) -> HTTPException:
    if isinstance(e, PlatformAuthError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    if isinstance(e, PlatformRateLimitError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


def _html(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body style='font-family: sans-serif; padding: 2rem;'><h2>{title}</h2><p>{message}</p>"
        f"<p>You can close this window.</p></body></html>"
    )


def _status_from_connection(connection: PlatformConnection | None) -> PlatformStatus:
    if connection is None or not connection.is_connected:
        return PlatformStatus(
            connected=False,
            account_id=None,
            shop_name=None,
            has_shop_icon=False,
            scopes=None,
            connected_at=None,
            sync_start_date=None,
            last_orders_synced_at=None,
            last_refreshed_at=None,
        )
    return PlatformStatus(
        connected=True,
        account_id=connection.external_account_id,
        shop_name=connection.shop_name,
        has_shop_icon=bool(connection.shop_icon_path),
        scopes=connection.scopes,
        connected_at=connection.connected_at,
        sync_start_date=connection.sync_start_date,
        last_orders_synced_at=connection.last_orders_synced_at,
        last_refreshed_at=connection.last_refreshed_at,
    )


async def _enrich_etsy_shop_details(connection: PlatformConnection, adapter, access_token: str) -> None:
    """Best-effort shop name/icon lookup — sets connection.shop_name/shop_icon_path when
    available, leaves them unset otherwise. Must never raise: called both from the OAuth
    callback (where a failure must not break the connect) and from /status (where a
    failure must not break reading connection state, and will simply retry next load)."""
    try:
        shop_name, icon_url = await adapter.fetch_shop_details(access_token, connection.external_account_id)
    except Exception:
        logger.exception("Failed to fetch Etsy shop details")
        return
    if shop_name:
        connection.shop_name = shop_name
    if icon_url:
        try:
            data, filename = await fetch_image_bytes(icon_url)
            connection.shop_icon_path = save_platform_icon(ListingPlatform.etsy.value, data, filename)
        except Exception:
            logger.exception("Failed to download Etsy shop icon")


@router.post("/{platform}/connect", response_model=PlatformConnectResponse, dependencies=[Depends(require_auth)])
async def connect_platform(platform: ListingPlatform) -> PlatformConnectResponse:
    adapter = get_adapter(platform)
    _cleanup_pending()

    # 43-128 chars of unreserved PKCE alphabet, base64url is a safe superset here.
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    _PENDING[state] = (code_verifier, time.time())

    url = adapter.build_authorize_url(state, code_challenge, _redirect_uri(platform), _SCOPES.get(platform, []))
    return PlatformConnectResponse(authorize_url=url)


@router.get("/{platform}/callback")
async def platform_callback(
    platform: ListingPlatform,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    # This endpoint is hit by the user's browser via the marketplace's redirect, not by
    # our authenticated Tauri client — it deliberately has no require_auth dependency.
    # The one-time `state` value (matched against the in-memory pending store) is what
    # proves this callback corresponds to a connect attempt we actually initiated.
    label = _PLATFORM_LABELS[platform]
    if error:
        return _html(f"{label} connection failed", f"{label} returned an error: {error}")
    if not code or not state or state not in _PENDING:
        return _html(f"{label} connection failed", "Missing or expired authorization state — please try connecting again.")

    code_verifier, _ = _PENDING.pop(state)
    adapter = get_adapter(platform)

    try:
        tokens = await adapter.exchange_code(code, code_verifier, _redirect_uri(platform))
        account_id = await adapter.fetch_account_id(tokens.access_token)
    except (PlatformAuthError, PlatformSyncError) as e:
        return _html(f"{label} connection failed", str(e))

    connection = await _get_or_create_connection(session, platform)
    connection.access_token = tokens.access_token
    connection.refresh_token = tokens.refresh_token
    connection.access_token_expires_at = tokens.expires_at
    connection.scopes = tokens.scopes
    connection.external_account_id = account_id
    connection.connected_at = datetime.now(timezone.utc)
    if platform == ListingPlatform.etsy:
        await _enrich_etsy_shop_details(connection, adapter, tokens.access_token)
    await session.commit()

    return _html(f"{label} connected", f"Account {account_id} is now connected to StockSmith.")


@router.get("/{platform}/status", response_model=PlatformStatus, dependencies=[Depends(require_auth)])
async def platform_status(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> PlatformStatus:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if platform == ListingPlatform.etsy and connection is not None and connection.is_connected and not connection.shop_name:
        adapter = get_adapter(platform)
        await _enrich_etsy_shop_details(connection, adapter, connection.access_token)
        await session.commit()
    return _status_from_connection(connection)


@router.get("/{platform}/shop-icon", dependencies=[Depends(require_auth)])
async def platform_shop_icon(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> FileResponse:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None or not connection.shop_icon_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No shop icon stored")
    path = resolve_asset_path(connection.shop_icon_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shop icon file missing on disk")
    return FileResponse(path)


@router.patch("/{platform}/sync-start-date", response_model=PlatformStatus, dependencies=[Depends(require_auth)])
async def update_sync_start_date(
    platform: ListingPlatform, payload: SyncStartDateUpdate, session: AsyncSession = Depends(get_db)
) -> PlatformStatus:
    """Moves the floor on order sync — fetch_orders_since never reaches earlier than
    this date, regardless of last_orders_synced_at (see services/order_sync.py)."""
    connection = await _require_connection(session, platform)
    connection.sync_start_date = payload.sync_start_date
    await session.commit()
    return _status_from_connection(connection)


@router.post("/{platform}/disconnect", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_auth)])
async def disconnect_platform(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> None:
    # Neither Etsy's nor eBay's public APIs expose a server-side token-revocation call
    # StockSmith can make — the access token expires naturally and the refresh token
    # becomes useless once dropped here, but a user wanting a full revoke must do so
    # from the marketplace's own connected-apps account settings. Clear every
    # connection-specific field, not just the tokens, so a future reconnect never
    # inherits a stale account id or sync watermark from a prior connection.
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None:
        return
    connection.access_token = None
    connection.refresh_token = None
    connection.access_token_expires_at = None
    connection.scopes = None
    connection.external_account_id = None
    connection.connected_at = None
    connection.last_orders_synced_at = None
    connection.last_refreshed_at = None
    if connection.shop_icon_path:
        resolve_asset_path(connection.shop_icon_path).unlink(missing_ok=True)
    connection.shop_name = None
    connection.shop_icon_path = None
    await session.commit()


@router.post("/{platform}/preview-sync", response_model=SyncPreviewResult, dependencies=[Depends(require_auth)])
async def preview_sync(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> SyncPreviewResult:
    """Fetches and shows what a real sync would do, without writing any order data —
    the safe way to sanity-check parsing/SKU-matching against a real store before
    trusting sync-orders to actually import anything."""
    try:
        return await order_sync.preview_sync(session, platform)
    except PlatformError as e:
        raise _map_platform_error(e)


@router.post("/{platform}/sync-orders", response_model=SyncCommitResult, dependencies=[Depends(require_auth)])
async def sync_orders(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> SyncCommitResult:
    try:
        return await order_sync.commit_sync(session, platform)
    except PlatformError as e:
        raise _map_platform_error(e)


@router.get("/{platform}/sync-log", response_model=SyncRunPage, dependencies=[Depends(require_auth)])
async def sync_log(
    platform: ListingPlatform,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> SyncRunPage:
    total = await session.scalar(
        select(func.count()).select_from(PlatformSyncRun).where(PlatformSyncRun.platform == platform)
    )
    result = await session.execute(
        select(PlatformSyncRun)
        .where(PlatformSyncRun.platform == platform)
        .order_by(PlatformSyncRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return SyncRunPage(items=list(result.scalars()), total=total or 0)


@router.post(
    "/{platform}/products/{product_id}/check-sync",
    response_model=ProductListingSyncSummary,
    dependencies=[Depends(require_auth)],
)
async def check_product_sync(
    platform: ListingPlatform, product_id: int, session: AsyncSession = Depends(get_db)
) -> ProductListingSyncSummary:
    """Tests the product's (or each active variant's) SKU against the marketplace's
    live listing catalog and persists the result — see services/listing_sync.py."""
    connection = await _require_connection(session, platform)
    adapter = get_adapter(platform)
    try:
        index = await adapter.build_listing_sku_index(session, connection)
        return await listing_sync.check_product_sku_sync(session, product_id, index, platform)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PlatformError as e:
        raise _map_platform_error(e)


@router.get(
    "/{platform}/products/{product_id}/sync-status",
    response_model=ProductListingSyncSummary,
    dependencies=[Depends(require_auth)],
)
async def get_product_sync_status(
    platform: ListingPlatform, product_id: int, session: AsyncSession = Depends(get_db)
) -> ProductListingSyncSummary:
    """Reads back the last check_product_sync result without contacting the
    marketplace — for page load."""
    try:
        return await listing_sync.get_stored_product_sync_status(session, product_id, platform)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get(
    "/{platform}/all-sync-status",
    response_model=dict[int, str],
    dependencies=[Depends(require_auth)],
)
async def get_all_sync_status(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> dict[int, str]:
    """Marketplace-free rollup per active product from stored Listing rows — for the
    products list badge column, which shouldn't trigger a live check just by being viewed."""
    statuses = await listing_sync.get_all_stored_sync_status(session, platform)
    return {product_id: s.value for product_id, s in statuses.items()}


@router.post(
    "/{platform}/check-all-listings",
    response_model=BulkListingSyncResult,
    dependencies=[Depends(require_auth)],
)
async def check_all_listings(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> BulkListingSyncResult:
    """Shop-wide SKU sync check across every active product — builds the listing index
    once and reuses it for every product, so this costs the same one marketplace fetch
    as a single-product check."""
    connection = await _require_connection(session, platform)
    adapter = get_adapter(platform)
    try:
        index = await adapter.build_listing_sku_index(session, connection)
        return await listing_sync.check_all_products_sku_sync(session, index, platform)
    except PlatformError as e:
        raise _map_platform_error(e)
