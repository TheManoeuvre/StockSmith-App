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

from app.deps import get_db, require_auth
from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.models.platform_listing_push import PlatformListingPush
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.listing import BulkListingSyncResult, ProductListingSyncSummary
from app.schemas.platform import (
    ListingPushPage,
    ListingPushRead,
    PlatformConnectResponse,
    PlatformCredentialRead,
    PlatformCredentialWrite,
    PlatformStatus,
    SyncCommitResult,
    SyncPreviewResult,
    SyncRunPage,
    SyncSettingsUpdate,
    SyncStartDateUpdate,
)
from app.services import listing_sync, order_sync, platform_credentials, sync_scheduler
from app.services.file_storage import resolve_asset_path, save_platform_icon
from app.services.platforms import get_adapter, invalidate_adapter_cache
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
# an intrinsic property of the marketplace's API. listings_w is now requested for Etsy
# and sell.inventory (write) for eBay — both push_listing_quantity implementations are
# in place (platforms/etsy.py, platforms/ebay.py); a connection made before these scopes
# were added needs to reconnect for pushes to work. sell.inventory.readonly stays
# alongside the write scope since it's what build_listing_sku_index was already granted
# under and there's no confirmation the write scope alone still covers reads.
_SCOPES: dict[ListingPlatform, list[str]] = {
    ListingPlatform.etsy: ["listings_r", "listings_w", "transactions_r"],
    ListingPlatform.ebay: [
        "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
        "https://api.ebay.com/oauth/api_scope/sell.finances",
        "https://api.ebay.com/oauth/api_scope/sell.inventory.readonly",
        "https://api.ebay.com/oauth/api_scope/sell.inventory",
        # Required for EbayAdapter.fetch_account_id's call to the Identity API
        # (commerce/identity/v1/user/) — confirmed missing after a live Sandbox connect
        # attempt 403'd with "Insufficient permissions to fulfill the request." Every
        # Sell scope above is unrelated to the Identity API and doesn't cover this.
        "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
    ],
}

# In-memory PKCE verifier/state store for the brief connect -> callback round trip. A
# module-level dict is sufficient here: this is a single-process desktop-app backend
# (no multi-instance deployment), and entries are short-lived — the user completes the
# marketplace's consent screen within a few minutes or the entry expires and connect
# must be retried. Non-PKCE adapters (eBay) simply never read the stored verifier back
# out — see EbayAdapter's own docstring.
_PENDING: dict[str, tuple[str, float, PlatformEnvironment]] = {}
_PENDING_TTL_SECONDS = 600


def _cleanup_pending() -> None:
    cutoff = time.time() - _PENDING_TTL_SECONDS
    for s in [s for s, (_, created_at, _env) in _PENDING.items() if created_at < cutoff]:
        _PENDING.pop(s, None)


async def _redirect_uri(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment = PlatformEnvironment.production
) -> str:
    """The value passed as OAuth `redirect_uri` — for URL-based platforms (Etsy) this
    is a literal callback URL; for eBay it's the opaque RuName eBay assigns to a
    redirect configuration registered in its dev portal (see platform_credentials.py's
    ru_name docstring) — the real callback URL is entered there once, not built
    dynamically. Resolved via platform_credentials (DB-stored, falling back to .env) so
    a per-install value works without a build-time secret-injection pipeline.
    `environment` only matters for eBay — Sandbox and Production each register their own
    RuName in eBay's dev portal."""
    if platform == ListingPlatform.ebay:
        ru_name = await platform_credentials.get_ebay_ru_name(session, environment)
        if not ru_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"eBay RuName ({environment.value}) is not configured"
            )
        return ru_name
    base_url = await platform_credentials.get_public_base_url(session, platform)
    if not base_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="public_base_url is not configured")
    return f"{base_url.rstrip('/')}/api/v1/platforms/{platform.value}/callback"


async def _get_or_create_connection(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment = PlatformEnvironment.production
) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = PlatformConnection(platform=platform, environment=environment)
        session.add(connection)
    else:
        # A fresh connect always specifies which environment to use — a reconnect that
        # switches Sandbox<->Production must overwrite this, not inherit whatever the
        # connection happened to be last time (its old tokens are being replaced anyway).
        connection.environment = environment
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


async def _latest_commit_run(session: AsyncSession, platform: ListingPlatform) -> PlatformSyncRun | None:
    """The most recent commit-mode sync attempt, success or failure — what
    PlatformStatus's last_sync_* fields are derived from. Preview runs are excluded since
    they never write data and aren't what auto-sync actually performs."""
    result = await session.execute(
        select(PlatformSyncRun)
        .where(PlatformSyncRun.platform == platform, PlatformSyncRun.mode == SyncRunMode.commit)
        .order_by(PlatformSyncRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _status_from_connection(
    session: AsyncSession, platform: ListingPlatform, connection: PlatformConnection | None
) -> PlatformStatus:
    if connection is None or not connection.is_connected:
        return PlatformStatus(
            connected=False,
            account_id=None,
            shop_name=None,
            has_shop_icon=False,
            scopes=None,
            environment=connection.environment if connection is not None else PlatformEnvironment.production,
            connected_at=None,
            sync_start_date=None,
            last_orders_synced_at=None,
            last_refreshed_at=None,
            auto_sync_enabled=False,
            sync_interval_minutes=connection.sync_interval_minutes if connection is not None else 15,
            last_sync_attempt_at=None,
            last_sync_success_at=None,
            last_sync_error=None,
        )

    latest_run = await _latest_commit_run(session, platform)
    last_sync_success_at = (
        latest_run.started_at if latest_run is not None and latest_run.status == SyncRunStatus.success else None
    )
    last_sync_error = (
        latest_run.error_message if latest_run is not None and latest_run.status == SyncRunStatus.error else None
    )

    return PlatformStatus(
        connected=True,
        account_id=connection.external_account_id,
        shop_name=connection.shop_name,
        has_shop_icon=bool(connection.shop_icon_path),
        scopes=connection.scopes,
        environment=connection.environment,
        connected_at=connection.connected_at,
        sync_start_date=connection.sync_start_date,
        last_orders_synced_at=connection.last_orders_synced_at,
        last_refreshed_at=connection.last_refreshed_at,
        auto_sync_enabled=connection.auto_sync_enabled,
        sync_interval_minutes=connection.sync_interval_minutes,
        last_sync_attempt_at=latest_run.started_at if latest_run is not None else None,
        last_sync_success_at=last_sync_success_at,
        last_sync_error=last_sync_error,
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
async def connect_platform(
    platform: ListingPlatform,
    environment: PlatformEnvironment = Query(PlatformEnvironment.production),
    session: AsyncSession = Depends(get_db),
) -> PlatformConnectResponse:
    # environment is ignored for Etsy (always 'production') — accepted uniformly so the
    # frontend doesn't need to special-case which platform it's calling.
    adapter = await get_adapter(session, platform, environment)
    _cleanup_pending()

    # 43-128 chars of unreserved PKCE alphabet, base64url is a safe superset here.
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    _PENDING[state] = (code_verifier, time.time(), environment)

    redirect_uri = await _redirect_uri(session, platform, environment)
    url = adapter.build_authorize_url(state, code_challenge, redirect_uri, _SCOPES.get(platform, []))
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

    code_verifier, _, environment = _PENDING.pop(state)
    adapter = await get_adapter(session, platform, environment)

    try:
        redirect_uri = await _redirect_uri(session, platform, environment)
        tokens = await adapter.exchange_code(code, code_verifier, redirect_uri)
        account_id = await adapter.fetch_account_id(tokens.access_token)
    except (PlatformAuthError, PlatformSyncError) as e:
        return _html(f"{label} connection failed", str(e))

    connection = await _get_or_create_connection(session, platform, environment)
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
        adapter = await get_adapter(session, platform)
        await _enrich_etsy_shop_details(connection, adapter, connection.access_token)
        await session.commit()
    return await _status_from_connection(session, platform, connection)


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
    return await _status_from_connection(session, platform, connection)


@router.patch("/{platform}/sync-settings", response_model=PlatformStatus, dependencies=[Depends(require_auth)])
async def update_sync_settings(
    platform: ListingPlatform, payload: SyncSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> PlatformStatus:
    """Toggles/configures the background auto-sync loop (services/sync_scheduler.py) for
    this platform. Enabling it also clears any prior consecutive-auth-failure count —
    the user re-enabling after fixing a reconnect issue shouldn't inherit a stale
    near-the-limit counter from before."""
    connection = await _require_connection(session, platform)
    if payload.auto_sync_enabled is not None:
        connection.auto_sync_enabled = payload.auto_sync_enabled
        if payload.auto_sync_enabled:
            connection.consecutive_auth_failures = 0
    if payload.sync_interval_minutes is not None:
        if payload.sync_interval_minutes < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sync_interval_minutes must be at least 1")
        connection.sync_interval_minutes = payload.sync_interval_minutes
    await session.commit()
    return await _status_from_connection(session, platform, connection)


@router.get("/{platform}/credentials", response_model=PlatformCredentialRead, dependencies=[Depends(require_auth)])
async def get_platform_credentials(
    platform: ListingPlatform,
    environment: PlatformEnvironment = Query(PlatformEnvironment.production),
    session: AsyncSession = Depends(get_db),
) -> PlatformCredentialRead:
    client_id, client_secret_set, public_base_url, ru_name = await platform_credentials.get_status(
        session, platform, environment
    )
    return PlatformCredentialRead(
        platform=platform,
        environment=environment,
        client_id=client_id,
        client_secret_set=client_secret_set,
        public_base_url=public_base_url,
        ru_name=ru_name,
    )


@router.patch("/{platform}/credentials", response_model=PlatformCredentialRead, dependencies=[Depends(require_auth)])
async def update_platform_credentials(
    platform: ListingPlatform,
    payload: PlatformCredentialWrite,
    environment: PlatformEnvironment = Query(PlatformEnvironment.production),
    session: AsyncSession = Depends(get_db),
) -> PlatformCredentialRead:
    await platform_credentials.upsert_credentials(
        session,
        platform,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
        public_base_url=payload.public_base_url,
        ru_name=payload.ru_name,
        environment=environment,
    )
    # A changed client id/secret must take effect on the very next request, not after a
    # process restart — the adapter registry caches by (platform, environment) (see
    # services/platforms/__init__.py), so it has to be dropped explicitly here.
    invalidate_adapter_cache(platform)
    client_id, client_secret_set, public_base_url, ru_name = await platform_credentials.get_status(
        session, platform, environment
    )
    return PlatformCredentialRead(
        platform=platform,
        environment=environment,
        client_id=client_id,
        client_secret_set=client_secret_set,
        public_base_url=public_base_url,
        ru_name=ru_name,
    )


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
    connection.auto_sync_enabled = False
    connection.consecutive_auth_failures = 0
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
    # Shares sync_scheduler's per-platform lock so a manual click can never run
    # concurrently with a background auto-sync tick for the same platform — unlike the
    # background loop (which skips its tick if the lock is already held), a manual click
    # waits for it, since the user explicitly asked for this to run now.
    async with sync_scheduler.get_lock(platform):
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


@router.get("/{platform}/listing-push-log", response_model=ListingPushPage, dependencies=[Depends(require_auth)])
async def listing_push_log(
    platform: ListingPlatform,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> ListingPushPage:
    """History of outbound quantity-push attempts (services/listing_push.py) — the push
    analog of /sync-log, which only covers inbound order sync. Surfaced so a persistently
    failing push is visible somewhere; see docs/plan-marketplace-integrations.md
    Section 1d on why a stale marketplace quantity is a real (not just cosmetic) risk."""
    total = await session.scalar(
        select(func.count()).select_from(PlatformListingPush).where(PlatformListingPush.platform == platform)
    )
    result = await session.execute(
        select(PlatformListingPush)
        .where(PlatformListingPush.platform == platform)
        .order_by(PlatformListingPush.attempted_at.desc())
        .limit(limit)
        .offset(offset)
    )
    pushes = list(result.scalars())

    product_ids = {p.product_id for p in pushes if p.product_id is not None}
    variant_ids = {p.variant_id for p in pushes if p.variant_id is not None}
    product_names = (
        dict((await session.execute(select(Product.id, Product.name).where(Product.id.in_(product_ids)))).all())
        if product_ids
        else {}
    )
    variant_names = (
        dict(
            (
                await session.execute(
                    select(ProductVariant.id, ProductVariant.variant_name).where(ProductVariant.id.in_(variant_ids))
                )
            ).all()
        )
        if variant_ids
        else {}
    )

    items = [
        ListingPushRead(
            id=p.id,
            product_id=p.product_id,
            product_name=product_names.get(p.product_id) if p.product_id is not None else None,
            variant_id=p.variant_id,
            variant_name=variant_names.get(p.variant_id) if p.variant_id is not None else None,
            platform=p.platform,
            attempted_qty=p.attempted_qty,
            status=p.status.value,
            error_message=p.error_message,
            attempted_at=p.attempted_at,
        )
        for p in pushes
    ]
    return ListingPushPage(items=items, total=total or 0)


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
    adapter = await get_adapter(session, platform)
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
    adapter = await get_adapter(session, platform)
    try:
        index = await adapter.build_listing_sku_index(session, connection)
        return await listing_sync.check_all_products_sku_sync(session, index, platform)
    except PlatformError as e:
        raise _map_platform_error(e)
