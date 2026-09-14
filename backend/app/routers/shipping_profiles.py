from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.listing import ListingPlatform
from app.models.shipping_profile import ShippingProfile
from app.routers._reference_crud import MergeRequest, delete_row, merge_rows
from app.schemas.shipping_profile import (
    MarketplaceShippingProfileRead,
    ShippingProfileCreate,
    ShippingProfileRead,
    ShippingProfileUpdate,
)
from app.services import reference_data, shipping_price_sync
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
