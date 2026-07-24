from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.services import platform_credentials
from app.services.platforms.base import PlatformAdapter
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.etsy import EtsyAdapter

# Cache is keyed on (platform, environment) — invalidate_adapter_cache() must be called
# after any credential save so a client id/secret change takes effect without a process
# restart. Constructing an adapter is cheap (just stores a couple of strings), but the
# cache still matters: EtsyAdapter holds a real asyncio.Lock in self._refresh_lock that
# only actually serializes concurrent token refreshes if every caller shares the same
# instance — a fresh adapter per request would silently drop that protection.
_adapters: dict[tuple[ListingPlatform, PlatformEnvironment], PlatformAdapter] = {}

_LABELS: dict[ListingPlatform, str] = {ListingPlatform.etsy: "Etsy", ListingPlatform.ebay: "eBay"}


def invalidate_adapter_cache(platform: ListingPlatform) -> None:
    for environment in PlatformEnvironment:
        _adapters.pop((platform, environment), None)


async def _resolve_environment(session: AsyncSession, platform: ListingPlatform) -> PlatformEnvironment:
    """Etsy has no sandbox — always production. For eBay, an already-connected shop's
    environment is authoritative (tokens are only valid for the host they were issued
    against); callers still setting up a *new* connection pass environment explicitly to
    get_adapter instead of relying on this."""
    if platform != ListingPlatform.ebay:
        return PlatformEnvironment.production
    result = await session.execute(select(PlatformConnection.environment).where(PlatformConnection.platform == platform))
    return result.scalar_one_or_none() or PlatformEnvironment.production


async def get_adapter(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment | None = None
) -> PlatformAdapter:
    """Registry keyed on ListingPlatform — core sync/allocation logic depends on the
    PlatformAdapter Protocol, not on this function's internals, so adding a new
    marketplace is additive here only: a new adapter class + one branch.

    Credentials are resolved per call (DB-stored, falling back to .env — see
    platform_credentials.py) rather than read once at import time, so editing them in
    Settings takes effect on the next request. `environment` is only meaningful for eBay
    (Sandbox vs. Production); omit it to use whatever the platform's existing connection
    is already using, or pass it explicitly when initiating a brand-new connection (see
    routers/platforms.connect_platform)."""
    if platform not in _LABELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported platform: {platform.value}")

    resolved_environment = environment or await _resolve_environment(session, platform)
    cache_key = (platform, resolved_environment)
    if cache_key in _adapters:
        return _adapters[cache_key]

    client_id, client_secret = await platform_credentials.get_client_credentials(
        session, platform, resolved_environment
    )
    if not client_id or not client_secret:
        env_label = f" ({resolved_environment.value})" if platform == ListingPlatform.ebay else ""
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{_LABELS[platform]}{env_label} is not configured — add its Client ID/Secret in Settings > Integrations",
        )

    adapter: PlatformAdapter
    if platform == ListingPlatform.etsy:
        adapter = EtsyAdapter(client_id, client_secret)
    else:
        adapter = EbayAdapter(client_id, client_secret, resolved_environment)

    _adapters[cache_key] = adapter
    return adapter
