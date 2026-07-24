from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.listing import ListingPlatform
from app.models.platform_credential import PlatformAppCredential, PlatformEnvironment

"""Resolves the app-level marketplace credentials (Client ID/Secret, redirect config)
adapters and the OAuth flow need — DB-stored rows (Settings > Integrations) take
priority, falling back to the .env-based Settings singleton so the `uv run uvicorn` dev
loop keeps working unchanged without ever needing a DB row. The packaged desktop app has
no .env, so the DB is the only path there — see docs/plan-marketplace-integrations.md
Section 1a for why."""


async def _get_row(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment
) -> PlatformAppCredential | None:
    result = await session.execute(
        select(PlatformAppCredential).where(
            PlatformAppCredential.platform == platform, PlatformAppCredential.environment == environment
        )
    )
    return result.scalar_one_or_none()


async def get_client_credentials(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment = PlatformEnvironment.production
) -> tuple[str | None, str | None]:
    row = await _get_row(session, platform, environment)
    if row is not None and row.client_id and row.client_secret:
        return row.client_id, row.client_secret
    if platform == ListingPlatform.etsy:
        return settings.etsy_client_id, settings.etsy_client_secret
    if platform == ListingPlatform.ebay:
        return settings.ebay_client_id, settings.ebay_client_secret
    return None, None


async def get_public_base_url(session: AsyncSession, platform: ListingPlatform) -> str | None:
    row = await _get_row(session, platform, PlatformEnvironment.production)
    if row is not None and row.public_base_url:
        return row.public_base_url
    return settings.public_base_url


async def get_ebay_ru_name(
    session: AsyncSession, environment: PlatformEnvironment = PlatformEnvironment.production
) -> str | None:
    row = await _get_row(session, ListingPlatform.ebay, environment)
    if row is not None and row.ru_name:
        return row.ru_name
    # .env's ebay_ru_name is a Production-portal value — no sensible Sandbox fallback,
    # so only fall back to it for the Production environment.
    return settings.ebay_ru_name if environment == PlatformEnvironment.production else None


async def get_status(
    session: AsyncSession, platform: ListingPlatform, environment: PlatformEnvironment = PlatformEnvironment.production
) -> tuple[str | None, bool, str | None, str | None]:
    """Read-only view for the Settings UI: (client_id, client_secret_set, public_base_url,
    ru_name) — never returns the secret itself, only whether one is stored, since it's
    never safe to echo a write-only credential back to the client."""
    row = await _get_row(session, platform, environment)
    if row is None:
        return None, False, None, None
    return row.client_id, bool(row.client_secret), row.public_base_url, row.ru_name


async def upsert_credentials(
    session: AsyncSession,
    platform: ListingPlatform,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    public_base_url: str | None = None,
    ru_name: str | None = None,
    environment: PlatformEnvironment = PlatformEnvironment.production,
) -> PlatformAppCredential:
    """Partial update — only fields actually passed (non-None) are written, so a save
    that omits client_secret (the common case: the UI never re-displays a stored secret,
    so there's nothing to resubmit unless the user is changing it) leaves the existing
    one untouched rather than blanking it."""
    row = await _get_row(session, platform, environment)
    if row is None:
        row = PlatformAppCredential(platform=platform, environment=environment)
        session.add(row)
    if client_id is not None:
        row.client_id = client_id
    if client_secret is not None:
        row.client_secret = client_secret
    if public_base_url is not None:
        row.public_base_url = public_base_url
    if ru_name is not None:
        row.ru_name = ru_name
    await session.commit()
    return row
