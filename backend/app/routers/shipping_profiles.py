from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from decimal import Decimal

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.shipping_profile import PriceEventSource, ShippingProfile, ShippingProfilePriceEvent
from app.routers._reference_crud import MergeRequest, delete_row, merge_rows
from app.schemas.shipping_profile import (
    MarketplaceShippingProfileRead,
    PriceRefreshChange,
    PriceRefreshResult,
    PriceRefreshSettingsUpdate,
    PriceRefreshStatus,
    ShippingProfileCreate,
    ShippingProfilePriceEventRead,
    ShippingProfileRead,
    ShippingProfileUpdate,
)
from app.services import reference_data, shipping_price_sync, sync_scheduler
from app.services.platforms.errors import PlatformAuthError, PlatformError, PlatformRateLimitError

router = APIRouter(prefix="/shipping-profiles", tags=["shipping-profiles"], dependencies=[Depends(require_auth)])

_LINK_FIELDS = ("etsy_shipping_profile_id", "ebay_fulfillment_policy_id")


def _map_platform_error(e: PlatformError) -> HTTPException:
    """Same statuses routers/platforms.py uses, so the frontend's reconnect-required
    handling (401) and rate-limit wording (429) apply here unchanged."""
    if isinstance(e, PlatformAuthError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    if isinstance(e, PlatformRateLimitError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


def _link_conflict(payload: dict) -> HTTPException:
    """A marketplace profile can back only one local profile — the unique indexes on the
    two link columns enforce it, and this turns the IntegrityError into a sentence."""
    which = "Etsy shipping profile" if payload.get("etsy_shipping_profile_id") is not None else "eBay postage policy"
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Another shipping profile is already linked to that {which}. Unlink it there first.",
    )


@router.get("", response_model=list[ShippingProfileRead])
async def list_shipping_profiles(
    include_archived: bool = False, session: AsyncSession = Depends(get_db)
) -> list[ShippingProfile]:
    """Active profiles by default.

    Archived ones are excluded rather than deleted so the pickers stay short while every order,
    product and variant that already points at one keeps resolving.
    """
    query = select(ShippingProfile).order_by(ShippingProfile.name)
    if not include_archived:
        query = query.where(ShippingProfile.is_archived.is_(False))
    profiles = list((await session.execute(query)).scalars())

    counts = await reference_data.usage_counts(session, ShippingProfile)
    for profile in profiles:
        profile.usage_count = counts.get(profile.id, 0)
    return profiles


@router.post("", response_model=ShippingProfileRead, status_code=status.HTTP_201_CREATED)
async def create_shipping_profile(
    payload: ShippingProfileCreate, session: AsyncSession = Depends(get_db)
) -> ShippingProfile:
    data = payload.model_dump()
    profile = ShippingProfile(**data)
    session.add(profile)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _link_conflict(data)
    await session.refresh(profile)
    profile.usage_count = 0
    return profile


@router.patch("/{profile_id}", response_model=ShippingProfileRead)
async def update_shipping_profile(
    profile_id: int, payload: ShippingProfileUpdate, session: AsyncSession = Depends(get_db)
) -> ShippingProfile:
    profile = await session.get(ShippingProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipping profile not found")
    data = payload.model_dump(exclude_unset=True)
    # A hand-typed per-channel price is a price change like any other and belongs in the
    # same history as the refresh's, or the trail has holes exactly where a human
    # intervened.
    for platform in (ListingPlatform.etsy, ListingPlatform.ebay):
        attribute = shipping_price_sync.price_attribute(platform)
        if attribute in data:
            old = getattr(profile, attribute)
            await shipping_price_sync.record_price_change(
                session,
                profile,
                platform,
                Decimal(old) if old is not None else None,
                data[attribute],
                PriceEventSource.user_edit,
            )
    for field, value in data.items():
        setattr(profile, field, value)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _link_conflict({k: data.get(k) for k in _LINK_FIELDS})
    await session.refresh(profile)
    profile.usage_count = (await reference_data.usage_counts(session, ShippingProfile)).get(profile.id, 0)
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shipping_profile(profile_id: int, session: AsyncSession = Depends(get_db)) -> None:
    """Only when nothing references it.

    Previously unconditional, which meant deleting a profile silently NULLed it on every product,
    variant and — worse — every historical order that had shipped under it, quietly changing what
    those orders say happened. Archiving is the operation people actually want.
    """
    await delete_row(session, ShippingProfile, profile_id)


@router.post("/{profile_id}/merge", response_model=ShippingProfileRead)
async def merge_shipping_profile(
    profile_id: int, payload: MergeRequest, session: AsyncSession = Depends(get_db)
) -> ShippingProfile:
    """Fold a duplicate profile into another. Refused if any order references it — see
    services/reference_data.py's `historical` flag."""
    return await merge_rows(session, ShippingProfile, profile_id, payload)


# --- Marketplace link ---------------------------------------------------------------------


@router.get("/marketplace/{platform}", response_model=list[MarketplaceShippingProfileRead])
async def list_marketplace_shipping_profiles(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> list[MarketplaceShippingProfileRead]:
    """The marketplace's own shipping profiles / postage policies, with the buyer price
    each currently charges for its domestic destination — what the link picker offers and
    what the drift marker compares a stored per-channel price against.

    400 when the platform is not connected, so the picker can disable itself with a hint
    rather than show an error."""
    try:
        profiles = await shipping_price_sync.fetch_marketplace_profiles(session, platform)
    except shipping_price_sync.NotConnectedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PlatformError as e:
        raise _map_platform_error(e)
    return [
        MarketplaceShippingProfileRead(
            id=p.id,
            title=p.title,
            is_calculated=p.is_calculated,
            domestic_price=p.domestic_price,
            domestic_fallback=p.domestic_fallback,
        )
        for p in profiles
    ]


@router.post("/{profile_id}/import-price/{platform}", response_model=ShippingProfileRead)
async def import_shipping_profile_price(
    profile_id: int, platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> ShippingProfile:
    """Pulls the linked marketplace profile's buyer price into price_<platform>.

    Explicit rather than a side effect of linking, so a margin never changes on the way
    past. 404 when the profile carries no link for this platform (or the link points at a
    profile the marketplace no longer has); 409 when the marketplace profile is calculated,
    since there is nothing fixed to import; a PlatformSyncError surfaces as the
    reconnect-required blocker it already is elsewhere."""
    profile = await session.get(ShippingProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipping profile not found")
    try:
        result = await shipping_price_sync.import_price(session, profile, platform)
    except shipping_price_sync.NotLinkedError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except shipping_price_sync.MissingUpstreamError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except shipping_price_sync.CalculatedProfileError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except shipping_price_sync.NotConnectedError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PlatformError as e:
        raise _map_platform_error(e)
    profile.usage_count = (await reference_data.usage_counts(session, ShippingProfile)).get(profile.id, 0)
    return profile


# --- Scheduled refresh: status, manual trigger, history --------------------------------


def _refresh_result_read(result: shipping_price_sync.RefreshResult) -> PriceRefreshResult:
    return PriceRefreshResult(
        platform=result.platform,
        refreshed_at=result.refreshed_at,
        changed=[
            PriceRefreshChange(
                shipping_profile_id=c.profile.id, name=c.profile.name, old_price=c.old_price, new_price=c.new_price
            )
            for c in result.changed
        ],
        unchanged_count=len(result.unchanged),
        skipped_calculated=[p.name for p in result.skipped_calculated],
        missing_upstream=[p.name for p in result.missing_upstream],
        error=result.error,
    )


@router.get("/refresh-status", response_model=list[PriceRefreshStatus])
async def get_price_refresh_status(session: AsyncSession = Depends(get_db)) -> list[PriceRefreshStatus]:
    """Per platform: the refresh cadence and when the last one ran, so the Settings page
    can say "last refreshed from Etsy 3 hours ago" next to the button."""
    connections = {
        c.platform: c for c in (await session.execute(select(PlatformConnection))).scalars()
    }
    out = []
    for platform in (ListingPlatform.etsy, ListingPlatform.ebay):
        connection = connections.get(platform)
        connected = connection is not None and connection.is_connected
        linked = await shipping_price_sync._linked_profiles(session, platform)
        out.append(
            PriceRefreshStatus(
                platform=platform,
                connected=connected,
                linked_count=len(linked),
                shipping_price_refresh_hours=connection.shipping_price_refresh_hours if connection else None,
                last_shipping_price_refresh_at=connection.last_shipping_price_refresh_at if connection else None,
            )
        )
    return out


@router.patch("/refresh-status/{platform}", response_model=PriceRefreshStatus)
async def update_price_refresh_settings(
    platform: ListingPlatform, payload: PriceRefreshSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> PriceRefreshStatus:
    if payload.shipping_price_refresh_hours < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shipping_price_refresh_hours must be at least 1")
    connection = (
        await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    ).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{platform.value} is not connected")
    connection.shipping_price_refresh_hours = payload.shipping_price_refresh_hours
    await session.commit()
    linked = await shipping_price_sync._linked_profiles(session, platform)
    return PriceRefreshStatus(
        platform=platform,
        connected=connection.is_connected,
        linked_count=len(linked),
        shipping_price_refresh_hours=connection.shipping_price_refresh_hours,
        last_shipping_price_refresh_at=connection.last_shipping_price_refresh_at,
    )


@router.post("/refresh-prices/{platform}", response_model=PriceRefreshResult)
async def refresh_shipping_prices(platform: ListingPlatform, session: AsyncSession = Depends(get_db)) -> PriceRefreshResult:
    """Runs the same refresh the background tick does, now, without waiting for the
    window. Takes the platform's sync lock so it never interleaves with a sync in flight
    (the manual endpoint waits, as "Sync now" does). A rate limit or read failure comes
    back in `error` with nothing written; auth failures are the reconnect blocker."""
    if platform not in (ListingPlatform.etsy, ListingPlatform.ebay):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{platform.value} has no shipping profile integration")
    async with sync_scheduler.get_lock(platform):
        try:
            result = await shipping_price_sync.refresh(session, platform)
        except shipping_price_sync.NotConnectedError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        except PlatformError as e:
            raise _map_platform_error(e)
    return _refresh_result_read(result)


@router.get("/{profile_id}/price-events", response_model=list[ShippingProfilePriceEventRead])
async def list_shipping_profile_price_events(
    profile_id: int, limit: int = 50, session: AsyncSession = Depends(get_db)
) -> list[ShippingProfilePriceEvent]:
    """The profile's per-channel price history, newest first."""
    if await session.get(ShippingProfile, profile_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipping profile not found")
    result = await session.execute(
        select(ShippingProfilePriceEvent)
        .where(ShippingProfilePriceEvent.shipping_profile_id == profile_id)
        .order_by(ShippingProfilePriceEvent.changed_at.desc(), ShippingProfilePriceEvent.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    return list(result.scalars())
