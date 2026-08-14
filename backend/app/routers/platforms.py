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
from app.models.platform_credential import PlatformAppCredential, PlatformEnvironment
from app.models.platform_listing_push import PlatformListingPush
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.listing import BulkListingSyncResult, ProductListingSyncSummary, PushCorrectionsResult
from app.schemas.platform import (
    EbaySigningKeyStatus,
    ListingPushPage,
    ListingPushRead,
    PlatformConnectResponse,
    PlatformCredentialRead,
    PlatformCredentialWrite,
    PlatformStatus,
    PlatformSyncSummary,
    SyncCommitResult,
    SyncPreviewResult,
    SyncRunPage,
    SyncSettingsUpdate,
    SyncStartDateUpdate,
)
from app.schemas.platform_limits import CatalogueCompatibilityReport
from app.schemas.listing_profile import (
    DraftReadinessReport,
    ProductPlatformSettingsRead,
    ProductPlatformSettingsWrite,
    ReadinessIssue,
)
from app.schemas.etsy_backfill import (
    ApplyProfileProposalsRequest,
    ApplyProfileProposalsResult,
    EtsyBackfillPreview,
    EtsyBackfillRequest,
    EtsyBackfillResult,
    ProductBackfillProposal,
    ProfileProposalRead,
    ProfileProposalsRead,
    VariantPriceProposal,
)
from app.schemas.listing_adoption import (
    AdoptListingRequest,
    AdoptListingResult,
    EligibilityAnnotatedCandidate,
    EtsyAdoptListingRequest,
    UnadoptedListing,
    UnadoptedListingProduct,
    UnadoptedListingsReport,
    UnmigratedListingsReport,
    VariationMappingProposal,
)
from app.services import (
    catalogue_compatibility,
    draft_readiness,
    etsy_backfill,
    listing_adoption,
    listing_profile_backfill,
    listing_copy,
    listing_profiles,
    listing_push,
    listing_sync,
    order_sync,
    platform_credentials,
    sync_scheduler,
    sync_status,
)
from app.services.file_storage import resolve_asset_path, save_platform_icon
from app.services.platforms import get_adapter, invalidate_adapter_cache
from app.services.platforms.base import ClassicListingCandidate
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.etsy import EtsyAdapter
from app.services.platforms.errors import PlatformAuthError, PlatformError, PlatformRateLimitError, PlatformSyncError
from app.services.url_import import fetch_image_bytes
from app.services.variants import compute_full_sku

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
        # The base scope, required for the legacy Trading API calls the unmigrated-listing
        # adoption feature depends on (GetMyeBaySelling, GetItem, GetUser,
        # ReviseFixedPriceItem — see platforms/ebay.py). None of the Sell REST scopes
        # above grant Trading API access; without this every Trading call fails auth.
        # A connection made before this scope was added must reconnect — see
        # _MISSING_TRADING_SCOPE_HINT and _has_trading_scope below.
        "https://api.ebay.com/oauth/api_scope",
    ],
}

# Compared against PlatformConnection.scopes to tell "this connection predates the
# Trading API scope" apart from a genuine API failure. Kept here (not on the adapter)
# for the same reason _SCOPES itself is: which scopes StockSmith asks for is a policy
# decision, not an intrinsic property of eBay's API.
_TRADING_SCOPE = "https://api.ebay.com/oauth/api_scope"

_MISSING_TRADING_SCOPE_HINT = (
    "This eBay connection was authorised before StockSmith requested the permission needed to read "
    "your Seller Hub listings. Reconnect eBay in Settings > Integrations, then try again."
)


def _has_trading_scope(connection: PlatformConnection) -> bool:
    """Whether this connection is KNOWN to be missing eBay's base api_scope.

    Deliberately fails OPEN when the granted scopes aren't recorded at all. This is a
    diagnostic that exists to turn a confusing downstream 401 into a clear instruction —
    it is not a security gate, and eBay enforces the real thing regardless. Treating
    "unknown" as "missing" made it assert a problem it couldn't actually see: eBay's
    token endpoint doesn't return a `scope` field at all, so connection.scopes was NULL
    for every eBay connection ever made, and the banner latched on permanently with
    reconnecting powerless to clear it. Fixed at the source too (the callback now falls
    back to recording the scopes we requested), but old rows keep the NULL, and a
    connection that genuinely has the scope must not be told otherwise."""
    granted = (connection.scopes or "").split()
    if not granted:
        return True  # unknown, not absent — let the API be the judge
    # Every Sell scope is a longer string that *starts with* the base scope, so a plain
    # substring test would match even when only e.g. sell.inventory was granted. Compare
    # whitespace-separated tokens exactly.
    return _TRADING_SCOPE in granted


def _require_trading_scope(connection: PlatformConnection) -> None:
    if not _has_trading_scope(connection):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_MISSING_TRADING_SCOPE_HINT)


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


def _map_trading_error(e: PlatformError) -> HTTPException:
    """As _map_platform_error, but an auth failure on a Trading API call gets the
    reconnect instruction attached.

    Now that the scope pre-check fails open on unrecorded scopes (see
    _has_trading_scope), this is where a genuinely missing api_scope actually surfaces —
    so the guidance has to live here too, or the user would just get eBay's own opaque
    401 with nothing actionable in it."""
    if isinstance(e, PlatformAuthError):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"{e}\n\n{_MISSING_TRADING_SCOPE_HINT}"
        )
    return _map_platform_error(e)


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
            unpaid_hold_since=None,
        )

    latest_run = await _latest_commit_run(session, platform)
    last_sync_success_at = (
        latest_run.started_at if latest_run is not None and latest_run.status == SyncRunStatus.success else None
    )
    last_sync_error = (
        latest_run.error_message if latest_run is not None and latest_run.status == SyncRunStatus.error else None
    )
    # eBay-only: Etsy asks for every scope it needs up front and has no Trading-API
    # equivalent, so its connections can never be in this state.
    needs_reconnect = platform == ListingPlatform.ebay and not _has_trading_scope(connection)

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
        unpaid_hold_since=connection.unpaid_hold_since,
        needs_reconnect=needs_reconnect,
        needs_reconnect_reason=_MISSING_TRADING_SCOPE_HINT if needs_reconnect else None,
    )


async def _signing_key_status(session: AsyncSession, environment: PlatformEnvironment) -> EbaySigningKeyStatus:
    """Never returns the private key itself — only whether one is stored, plus the
    identifiers eBay's own dev portal shows, so a key can be matched up by eye."""
    result = await session.execute(
        select(PlatformAppCredential).where(
            PlatformAppCredential.platform == ListingPlatform.ebay,
            PlatformAppCredential.environment == environment,
        )
    )
    row = result.scalar_one_or_none()
    return EbaySigningKeyStatus(
        environment=environment,
        configured=bool(row is not None and row.signing_key_jwe and row.signing_key_private),
        signing_key_id=row.signing_key_id if row is not None else None,
        created_at=row.signing_key_created_at if row is not None else None,
        expires_at=row.signing_key_expires_at if row is not None else None,
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


# Declared before the /{platform}/... routes so "sync-summary" can never be parsed as a
# platform name, and kept as a single segment so it doesn't collide with them at all.
@router.get("/sync-summary", response_model=list[PlatformSyncSummary], dependencies=[Depends(require_auth)])
async def get_sync_summary(session: AsyncSession = Depends(get_db)) -> list[PlatformSyncSummary]:
    """Sync health across every connected platform, for the menu-bar indicator.

    Local reads only — unlike /{platform}/status this never touches a marketplace, which
    is what makes it safe for the UI to poll on a timer."""
    return await sync_status.get_sync_summary(session)


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
    # eBay's token endpoint doesn't echo a `scope` field back at all, so trusting the
    # response alone left connection.scopes NULL for every eBay connection. Fall back to
    # what we asked for: the grant only completes if the user approved that consent
    # screen, so the requested set is what was granted. Etsy does return it, and its
    # value wins where present since a user can in principle approve a subset.
    connection.scopes = tokens.scopes or " ".join(_SCOPES.get(platform, [])) or None
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


@router.get("/ebay/signing-key", response_model=EbaySigningKeyStatus, dependencies=[Depends(require_auth)])
async def get_ebay_signing_key_status(
    environment: PlatformEnvironment = Query(PlatformEnvironment.production),
    session: AsyncSession = Depends(get_db),
) -> EbaySigningKeyStatus:
    return await _signing_key_status(session, environment)


@router.post("/ebay/signing-key", response_model=EbaySigningKeyStatus, dependencies=[Depends(require_auth)])
async def create_ebay_signing_key(
    environment: PlatformEnvironment = Query(PlatformEnvironment.production),
    session: AsyncSession = Depends(get_db),
) -> EbaySigningKeyStatus:
    """Mints the Ed25519 keypair eBay requires to sign requests to its in-scope APIs.

    Not idempotent, and deliberately not guarded against re-running: eBay allows several
    live keypairs per keyset and the newest simply supersedes the one stored here, so a
    second call costs an orphaned key on eBay's side and nothing else. The response's
    privateKey is returned exactly once and eBay retains no copy, so it is persisted
    before this function does anything else with it.
    """
    adapter = await get_adapter(session, ListingPlatform.ebay, environment)
    if not isinstance(adapter, EbayAdapter):  # pragma: no cover — get_adapter guarantees this
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signing keys are eBay-only")
    try:
        key_response = await adapter.create_signing_key()
    except PlatformError as e:
        raise _map_platform_error(e) from e
    await platform_credentials.store_ebay_signing_key(session, key_response, environment)
    # The cached adapter was built with the old key (or none) — same contract as a
    # credential edit, see update_platform_credentials.
    invalidate_adapter_cache(ListingPlatform.ebay)
    logger.info("Minted eBay signing key %s (%s)", key_response.get("signingKeyId"), environment.value)
    return await _signing_key_status(session, environment)


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
async def sync_orders(platform: ListingPlatform) -> SyncCommitResult:
    # Shares sync_scheduler's per-platform lock so a manual click can never run
    # concurrently with a background auto-sync tick for the same platform — unlike the
    # background loop (which skips its tick if the lock is already held), a manual click
    # waits for it, since the user explicitly asked for this to run now. No session
    # dependency here — commit_sync manages its own short-lived sessions internally (see
    # its docstring), so this endpoint doesn't need one of its own to hand it.
    async with sync_scheduler.get_lock(platform):
        try:
            return await order_sync.commit_sync(platform)
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


def _proposal_schema(proposal) -> ProductBackfillProposal:
    return ProductBackfillProposal(
        product_id=proposal.product_id,
        product_name=proposal.product_name,
        external_listing_id=proposal.external_listing_id,
        listing_title=proposal.listing_title,
        description=proposal.description,
        description_chars=proposal.description_chars,
        sale_price=proposal.sale_price,
        image_url=proposal.image_url,
        variant_prices=[
            VariantPriceProposal(
                variant_id=v.variant_id,
                variant_name=v.variant_name,
                sku=v.sku,
                proposed_price=v.proposed_price,
            )
            for v in proposal.variant_prices
        ],
    )


@router.get("/etsy/profile-proposals", response_model=ProfileProposalsRead, dependencies=[Depends(require_auth)])
async def get_etsy_profile_proposals(session: AsyncSession = Depends(get_db)) -> ProfileProposalsRead:
    """Listing profiles derived from the Etsy listings these products already have.

    A shop's catalogue spans a handful of genuine metadata combinations rather than one per
    product, so this proposes one profile per combination — turning profile setup into
    reviewing a few suggestions instead of looking nine fields up by hand."""
    adapter, connection = await _get_etsy_adapter(session)
    try:
        listings = await adapter.fetch_all_listings(session, connection)
    except PlatformError as e:
        raise _map_platform_error(e)

    proposals = await listing_profile_backfill.propose_profiles(session, listings)
    return ProfileProposalsRead(
        proposals=[
            ProfileProposalRead(
                index=index,
                suggested_name=p.suggested_name,
                is_complete=p.is_complete,
                product_count=len(p.product_ids),
                product_names=p.product_names[:5],
                taxonomy_id=p.signature.taxonomy_id,
                who_made=p.signature.who_made,
                when_made=p.signature.when_made,
                is_supply=p.signature.is_supply,
                shipping_profile_id=p.signature.shipping_profile_id,
                return_policy_id=p.signature.return_policy_id,
                processing_min=p.processing_min,
                processing_max=p.processing_max,
            )
            for index, p in enumerate(proposals)
        ]
    )


@router.post(
    "/etsy/profile-proposals/apply",
    response_model=ApplyProfileProposalsResult,
    dependencies=[Depends(require_auth)],
)
async def apply_etsy_profile_proposals(
    payload: ApplyProfileProposalsRequest, session: AsyncSession = Depends(get_db)
) -> ApplyProfileProposalsResult:
    """Creates the accepted proposals as profiles and points their products at them."""
    adapter, connection = await _get_etsy_adapter(session)
    try:
        listings = await adapter.fetch_all_listings(session, connection)
    except PlatformError as e:
        raise _map_platform_error(e)

    result = await listing_profile_backfill.apply_proposals(
        session,
        listings,
        {item.index: item.name for item in payload.items},
        assign_products=payload.assign_products,
    )
    return ApplyProfileProposalsResult(
        profiles_created=result.profiles_created, products_assigned=result.products_assigned
    )


@router.get("/etsy/backfill-preview", response_model=EtsyBackfillPreview, dependencies=[Depends(require_auth)])
async def get_etsy_backfill_preview(session: AsyncSession = Depends(get_db)) -> EtsyBackfillPreview:
    """What could be filled from the Etsy listings these products are already matched to.

    Reads only — nothing is written until the user picks. See services/etsy_backfill.py
    for the fill-blanks-only rule and why per-offering prices are used rather than the
    listing price."""
    adapter, connection = await _get_etsy_adapter(session)
    try:
        listings = await adapter.fetch_all_listings(session, connection, with_images=True)
    except PlatformError as e:
        raise _map_platform_error(e)

    preview = await etsy_backfill.build_preview(session, listings)
    return EtsyBackfillPreview(
        products=[_proposal_schema(p) for p in preview.products],
        already_complete=preview.already_complete,
        unmatched=preview.unmatched,
    )


@router.post("/etsy/backfill", response_model=EtsyBackfillResult, dependencies=[Depends(require_auth)])
async def apply_etsy_backfill(
    payload: EtsyBackfillRequest, session: AsyncSession = Depends(get_db)
) -> EtsyBackfillResult:
    """Applies the ticked fields for the ticked products.

    Re-crawls rather than trusting the previewed values: the preview the user was looking
    at may be minutes old, and this writes to their catalogue. Same reasoning as
    push_product_corrections re-deriving its quantities at send time."""
    adapter, connection = await _get_etsy_adapter(session)
    try:
        listings = await adapter.fetch_all_listings(session, connection, with_images=True)
    except PlatformError as e:
        raise _map_platform_error(e)

    selections = {item.product_id: set(item.fields) for item in payload.items}
    result = await etsy_backfill.apply_backfill(session, connection, listings, selections)
    return EtsyBackfillResult(
        products_updated=result.products_updated,
        descriptions_filled=result.descriptions_filled,
        prices_filled=result.prices_filled,
        images_filled=result.images_filled,
        errors=result.errors,
    )


async def _settings_schema(session, product_id: int, platform: ListingPlatform) -> ProductPlatformSettingsRead:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    settings = await listing_copy.get_settings(session, product_id, platform)
    resolved = listing_copy.resolve_copy(product, settings)
    return ProductPlatformSettingsRead(
        product_id=product_id,
        platform=platform,
        listing_profile_id=settings.listing_profile_id if settings else None,
        is_target=settings.is_target if settings else None,
        listing_title=settings.listing_title if settings else None,
        listing_description=settings.listing_description if settings else None,
        resolved_title=resolved.title,
        resolved_title_source=resolved.title_source,
        resolved_description=resolved.description,
        resolved_description_source=resolved.description_source,
    )


@router.get(
    "/{platform}/products/{product_id}/settings",
    response_model=ProductPlatformSettingsRead,
    dependencies=[Depends(require_auth)],
)
async def get_product_platform_settings(
    platform: ListingPlatform, product_id: int, session: AsyncSession = Depends(get_db)
) -> ProductPlatformSettingsRead:
    """This product's listing profile and copy for one platform, plus what each field
    resolves to once the fallback chain is applied."""
    return await _settings_schema(session, product_id, platform)


@router.put(
    "/{platform}/products/{product_id}/settings",
    response_model=ProductPlatformSettingsRead,
    dependencies=[Depends(require_auth)],
)
async def update_product_platform_settings(
    platform: ListingPlatform,
    product_id: int,
    payload: ProductPlatformSettingsWrite,
    session: AsyncSession = Depends(get_db),
) -> ProductPlatformSettingsRead:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    settings = await listing_profiles.get_or_create_settings(session, product_id, platform)
    settings.listing_profile_id = payload.listing_profile_id
    settings.is_target = payload.is_target
    # Empty strings are stored as NULL so the value falls back rather than resolving to a
    # blank title — clearing the box means "use the shared copy", not "publish nothing".
    settings.listing_title = (payload.listing_title or "").strip() or None
    settings.listing_description = (payload.listing_description or "").strip() or None
    await session.commit()
    return await _settings_schema(session, product_id, platform)


@router.get(
    "/{platform}/products/{product_id}/draft-readiness",
    response_model=DraftReadinessReport,
    dependencies=[Depends(require_auth)],
)
async def get_draft_readiness(
    platform: ListingPlatform, product_id: int, session: AsyncSession = Depends(get_db)
) -> DraftReadinessReport:
    """Whether a draft listing could be created for this product, and what is missing.

    Purely local — no adapter call, nothing spent. That is what lets it drive a button's
    enabled state directly, so the user never clicks "create draft" only to be told it was
    never possible."""
    report = await draft_readiness.evaluate(session, product_id, platform)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return DraftReadinessReport(
        product_id=report.product_id,
        platform=report.platform,
        can_create=report.can_create,
        profile_id=report.profile_id,
        profile_name=report.profile_name,
        title=report.title,
        title_source=report.title_source,
        description_chars=report.description_chars,
        unit_count=report.unit_count,
        priced_unit_count=report.priced_unit_count,
        image_count=report.image_count,
        issues=[
            ReadinessIssue(
                field=i.field, severity=i.severity, message=i.message, fix_hint=i.fix_hint
            )
            for i in report.issues
        ],
    )


@router.get(
    "/{platform}/catalogue-compatibility",
    response_model=CatalogueCompatibilityReport,
    dependencies=[Depends(require_auth)],
)
async def get_catalogue_compatibility(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> CatalogueCompatibilityReport:
    """Reports which existing products breach this platform's field limits.

    Deliberately a plain GET rather than a button-triggered mutation like the listing gap
    scan: this makes no marketplace call at all, so it costs nothing to load and there is
    no rate-limit budget to protect. A button nobody presses would just hide the answer.

    Also deliberately not a gate on check-sync. Those endpoints are read-only diagnostics,
    and refusing to run one because the data is non-conformant would withhold exactly the
    information needed to fix it. The gate belongs on the write paths.
    """
    return await catalogue_compatibility.scan_catalogue(session, platform)


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
        # Scoped to this product's own SKUs so a single-product check doesn't pay for a
        # per-SKU offer lookup across the whole catalogue.
        index = await adapter.build_listing_sku_index(
            session, connection, enrich_skus=await listing_sync.product_skus(session, product_id)
        )
        # Read-only: this reports a quantity drift, it does not correct it. Pushing from
        # a "test" action would make a diagnostic silently mutate the live marketplace —
        # correcting is the separate, explicitly-confirmed push-corrections endpoint.
        return await listing_sync.check_product_sku_sync(
            session, product_id, index, platform, with_expected_quantity=True
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PlatformError as e:
        raise _map_platform_error(e)


@router.post(
    "/{platform}/products/{product_id}/push-corrections",
    response_model=PushCorrectionsResult,
    dependencies=[Depends(require_auth)],
)
async def push_product_corrections(
    platform: ListingPlatform, product_id: int, session: AsyncSession = Depends(get_db)
) -> PushCorrectionsResult:
    """Pushes StockSmith's quantities to the marketplace for units whose listing quantity
    has drifted — the user-confirmed follow-up to a sync check that found mismatches.

    Deliberately re-derives which units are mismatched from stored state rather than
    accepting a list from the client: the check may be minutes old by the time the button
    is clicked, and pushing a unit the client *believed* was wrong risks overwriting a
    quantity that has since been corrected legitimately."""
    await _require_connection(session, platform)
    try:
        summary = await listing_sync.get_stored_product_sync_status(session, product_id, platform)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    mismatched = [unit.variant_id for unit in summary.units if unit.quantity_mismatch]
    if not mismatched:
        return PushCorrectionsResult(pushed_count=0, failed_count=0, errors=[])

    try:
        pushed, errors = await listing_push.push_units_now(session, product_id, mismatched)
    except PlatformError as e:
        raise _map_platform_error(e)
    return PushCorrectionsResult(pushed_count=pushed, failed_count=len(errors), errors=errors)


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
        index = await adapter.build_listing_sku_index(
            session, connection, enrich_skus=await listing_sync.tracked_skus(session)
        )
        return await listing_sync.check_all_products_sku_sync(session, index, platform)
    except PlatformError as e:
        raise _map_platform_error(e)


async def _get_ebay_adapter(session: AsyncSession) -> tuple[EbayAdapter, PlatformConnection]:
    connection = await _require_connection(session, ListingPlatform.ebay)
    _require_trading_scope(connection)
    adapter = await get_adapter(session, ListingPlatform.ebay)
    if not isinstance(adapter, EbayAdapter):
        # Not an assert: `python -O` strips those, and this guards a genuine runtime
        # contract (the Trading-API-only methods below don't exist on other adapters).
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="eBay adapter unavailable — the platform registry returned an unexpected adapter type",
        )
    return adapter, connection


async def _require_product(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product {product_id} not found")
    return product


def _expected_sku(product: Product, active_variants: list[ProductVariant], variant_id: int | None) -> str | None:
    if variant_id is None:
        return product.sku
    variant = next((v for v in active_variants if v.id == variant_id), None)
    return compute_full_sku(product.sku, variant.sku_suffix) if variant is not None else None


def _product_expected_skus(product: Product, active_variants: list[ProductVariant]) -> set[str]:
    if not active_variants:
        return {product.sku} if product.sku else set()
    return {compute_full_sku(product.sku, v.sku_suffix) for v in active_variants} - {None}


async def _load_candidate_detail(
    adapter: EbayAdapter, session: AsyncSession, connection: PlatformConnection, external_listing_id: str
) -> ClassicListingCandidate:
    """Authoritative per-listing detail via GetItem, plus the is_migrated flag the list
    view computes by cross-referencing the Inventory API.

    Everything that acts on a listing's SKUs goes through here rather than reusing the
    bulk-list payload: GetMyeBaySelling's ActiveList doesn't reliably carry variation
    SKUs (see EbayAdapter.fetch_classic_listing_detail), and re-running the whole
    paginated list crawl just to look one listing up would be far more expensive
    besides."""
    candidate = await adapter.fetch_classic_listing_detail(session, connection, external_listing_id)
    # Membership test only (is this candidate's SKU already migrated?) — no need to pay
    # for per-SKU offer lookups.
    index = await adapter.build_listing_sku_index(session, connection, enrich=False)
    candidate.is_migrated = any(sku in index for sku in candidate.skus if sku)
    return candidate


def _to_report(candidates: list[ClassicListingCandidate]) -> UnmigratedListingsReport:
    return UnmigratedListingsReport(
        total_count=len(candidates),
        eligible_count=sum(1 for c in candidates if not c.ineligibility_reasons),
        listings=[
            EligibilityAnnotatedCandidate(
                external_listing_id=c.external_listing_id,
                title=c.title,
                listing_type=c.listing_type,
                skus=[s for s in c.skus if s],
                variation_specifics=c.variation_specifics,
                quantity=c.quantity,
                ineligibility_reasons=c.ineligibility_reasons,
                detail_loaded=c.detail_loaded,
            )
            for c in candidates
        ],
    )


@router.get(
    "/ebay/unmigrated-listings",
    response_model=UnmigratedListingsReport,
    dependencies=[Depends(require_auth)],
)
async def get_unmigrated_listings(session: AsyncSession = Depends(get_db)) -> UnmigratedListingsReport:
    """Shop-wide classic (not-yet-migrated) eBay listings — powers the settings-level
    "X unmigrated listings" alert and its unscoped picker, where the user also has to
    choose which StockSmith product to link to."""
    adapter, connection = await _get_ebay_adapter(session)
    try:
        candidates = await adapter.fetch_classic_listings(session, connection)
    except PlatformError as e:
        raise _map_trading_error(e)
    return _to_report([c for c in candidates if not c.is_migrated])


@router.get(
    "/ebay/products/{product_id}/unmigrated-listings",
    response_model=UnmigratedListingsReport,
    dependencies=[Depends(require_auth)],
)
async def get_product_unmigrated_listings(
    product_id: int, session: AsyncSession = Depends(get_db)
) -> UnmigratedListingsReport:
    """Classic (not-yet-migrated) eBay listings, ranked by SKU match against this
    product, for the product-page 'find unmigrated listing' picker. See
    EbayAdapter.fetch_classic_listings for why this is a separate call from
    build_listing_sku_index (the Inventory API can't see these at all)."""
    product = await _require_product(session, product_id)
    active_variants = await listing_sync._active_variants(session, product_id)
    expected_skus = _product_expected_skus(product, active_variants)

    adapter, connection = await _get_ebay_adapter(session)
    try:
        candidates = await adapter.fetch_classic_listings(session, connection)
    except PlatformError as e:
        raise _map_trading_error(e)

    unmigrated = [c for c in candidates if not c.is_migrated]
    # Exact SKU match first, then a title containing the product name, then the rest —
    # a shop with hundreds of active listings otherwise makes the user hunt for theirs.
    name_key = (product.name or "").strip().lower()

    def rank(c: ClassicListingCandidate) -> int:
        if expected_skus & {s for s in c.skus if s}:
            return 0
        if name_key and name_key in c.title.lower():
            return 1
        return 2

    unmigrated.sort(key=rank)
    return _to_report(unmigrated)


@router.get(
    "/ebay/products/{product_id}/listings/{external_listing_id}/variation-mapping",
    response_model=VariationMappingProposal,
    dependencies=[Depends(require_auth)],
)
async def get_variation_mapping(
    product_id: int, external_listing_id: str, session: AsyncSession = Depends(get_db)
) -> VariationMappingProposal:
    """Proposes a StockSmith-variant -> eBay-SKU mapping for a selected classic
    listing — shown by the picker's mapping-editor step before adopt is enabled."""
    product = await _require_product(session, product_id)
    active_variants = await listing_sync._active_variants(session, product_id)

    adapter, connection = await _get_ebay_adapter(session)
    try:
        candidate = await _load_candidate_detail(adapter, session, connection, external_listing_id)
    except PlatformError as e:
        raise _map_trading_error(e)

    return listing_adoption.propose_variation_mapping(product, active_variants, candidate)


@router.post(
    "/ebay/products/{product_id}/adopt-listing",
    response_model=AdoptListingResult,
    dependencies=[Depends(require_auth)],
)
async def adopt_ebay_listing(
    product_id: int, body: AdoptListingRequest, session: AsyncSession = Depends(get_db)
) -> AdoptListingResult:
    """Migrates a classic eBay listing (bulkMigrateListing) and links it to this
    product/its variants per the user-confirmed variation_mapping.

    Order matters and is deliberate: when align_skus is set, the listing's SKUs are
    revised BEFORE migration, because a classic listing's SKU is freely editable whereas
    a migrated one's is effectively immutable (see EbayAdapter.revise_listing_skus).
    Without alignment, StockSmith's own SKU is still what gets written as the lookup key
    and the divergence is reported as a per-unit sku_conflict instead."""
    product = await _require_product(session, product_id)
    active_variants = await listing_sync._active_variants(session, product_id)
    if not body.variation_mapping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="variation_mapping must not be empty"
        )
    # Two variants claiming the same eBay SKU would produce two Listing rows pointing at
    # one marketplace object, so their quantity pushes would fight over it.
    mapped_skus = [choice.sku for choice in body.variation_mapping]
    if len(set(mapped_skus)) != len(mapped_skus):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each eBay SKU can only be linked to one StockSmith unit",
        )

    adapter, connection = await _get_ebay_adapter(session)
    variation_mapping = [(choice.variant_id, choice.sku) for choice in body.variation_mapping]

    try:
        candidate = await _load_candidate_detail(adapter, session, connection, body.external_listing_id)

        skus_aligned = False
        if body.align_skus and not candidate.is_migrated:
            desired = listing_adoption.plan_sku_alignment(product, active_variants, candidate, variation_mapping)
            if desired is not None:
                await adapter.revise_listing_skus(session, connection, candidate, desired)
                skus_aligned = True
                # The mapping the caller sent refers to the listing's pre-revision SKUs;
                # after aligning, every unit's eBay SKU is StockSmith's own, so re-point
                # the mapping at those to avoid reporting a conflict we just resolved.
                variation_mapping = [
                    (variant_id, _expected_sku(product, active_variants, variant_id) or actual)
                    for variant_id, actual in variation_mapping
                ]

        await adapter.migrate_listing(session, connection, body.external_listing_id)
    except PlatformError as e:
        raise _map_trading_error(e)

    return await listing_adoption.apply_adoption(
        session,
        product,
        active_variants,
        variation_mapping,
        ListingPlatform.ebay,
        body.listing_title or candidate.title,
        skus_aligned=skus_aligned,
    )


async def _get_etsy_adapter(session: AsyncSession) -> tuple[EtsyAdapter, PlatformConnection]:
    connection = await _require_connection(session, ListingPlatform.etsy)
    adapter = await get_adapter(session, ListingPlatform.etsy)
    if not isinstance(adapter, EtsyAdapter):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Etsy adapter unavailable — the platform registry returned an unexpected adapter type",
        )
    return adapter, connection


@router.get(
    "/etsy/unadopted-listings",
    response_model=UnadoptedListingsReport,
    dependencies=[Depends(require_auth)],
)
async def get_etsy_unadopted_listings(session: AsyncSession = Depends(get_db)) -> UnadoptedListingsReport:
    """Etsy listings carrying at least one SKU StockSmith doesn't recognise (or none at
    all) — the reverse of eBay's unmigrated case: the listing is perfectly visible, it's
    StockSmith's catalog that has the gap. See listing_adoption.find_unadopted_listings."""
    adapter, connection = await _get_etsy_adapter(session)
    try:
        raw_listings = await adapter.fetch_all_listings(session, connection)
    except PlatformError as e:
        raise _map_platform_error(e)

    candidates = [EtsyAdapter.parse_listing_products(listing) for listing in raw_listings]
    known = await listing_adoption.known_stocksmith_skus(session)
    unadopted = listing_adoption.find_unadopted_listings(candidates, known)

    return UnadoptedListingsReport(
        total_count=len(unadopted),
        listings=[
            UnadoptedListing(
                external_listing_id=c.external_listing_id,
                title=c.title,
                state=c.state,
                products=[
                    UnadoptedListingProduct(
                        index=p.index, sku=p.sku, variation=p.variation, quantity=p.quantity
                    )
                    for p in c.products
                ],
            )
            for c in unadopted
        ],
    )


@router.post(
    "/etsy/products/{product_id}/adopt-listing",
    response_model=AdoptListingResult,
    dependencies=[Depends(require_auth)],
)
async def adopt_etsy_listing(
    product_id: int, body: EtsyAdoptListingRequest, session: AsyncSession = Depends(get_db)
) -> AdoptListingResult:
    """Links an existing Etsy listing to this product, writing StockSmith's SKUs onto
    the listing so future sync checks resolve it.

    No migration step exists on Etsy, so unlike the eBay path this is purely a SKU write
    plus the local Listing rows."""
    product = await _require_product(session, product_id)
    active_variants = await listing_sync._active_variants(session, product_id)
    if not body.links:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="links must not be empty")

    # Two StockSmith units pointing at the same Etsy variation would collapse in
    # sku_by_index below — one SKU written, but two Listing rows created claiming it.
    indexes = [link.product_index for link in body.links]
    if len(set(indexes)) != len(indexes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Each Etsy variation can only be linked to one StockSmith unit",
        )

    sku_by_index: dict[int, str] = {}
    variation_mapping: list[tuple[int | None, str]] = []
    for link in body.links:
        expected = _expected_sku(product, active_variants, link.variant_id)
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot link an Etsy listing to a product/variant with no SKU — set a SKU in StockSmith first, "
                    "since that's the value written to Etsy."
                ),
            )
        sku_by_index[link.product_index] = expected
        variation_mapping.append((link.variant_id, expected))

    adapter, connection = await _get_etsy_adapter(session)
    if body.write_skus:
        try:
            await adapter.update_listing_skus(session, connection, body.external_listing_id, sku_by_index)
        except PlatformError as e:
            raise _map_platform_error(e)

    # Etsy's Listing rows key on the listing id (unlike eBay's, which key on SKU) —
    # see EtsyAdapter._index_listing_skus, which sets external_listing_id to listing_id.
    return await listing_adoption.apply_adoption(
        session,
        product,
        active_variants,
        variation_mapping,
        ListingPlatform.etsy,
        body.listing_title or "",
        external_listing_id=body.external_listing_id,
    )
