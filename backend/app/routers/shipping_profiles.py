from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.shipping_profile import ShippingProfile
from app.routers._reference_crud import MergeRequest, delete_row, merge_rows
from app.schemas.shipping_profile import ShippingProfileCreate, ShippingProfileRead, ShippingProfileUpdate
from app.services import reference_data

router = APIRouter(prefix="/shipping-profiles", tags=["shipping-profiles"], dependencies=[Depends(require_auth)])


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
    profile = ShippingProfile(**payload.model_dump())
    session.add(profile)
    await session.commit()
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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await session.commit()
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
