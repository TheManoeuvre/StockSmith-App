from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.shipping_profile import ShippingProfile
from app.schemas.shipping_profile import ShippingProfileCreate, ShippingProfileRead, ShippingProfileUpdate

router = APIRouter(prefix="/shipping-profiles", tags=["shipping-profiles"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[ShippingProfileRead])
async def list_shipping_profiles(session: AsyncSession = Depends(get_db)) -> list[ShippingProfile]:
    result = await session.execute(select(ShippingProfile).order_by(ShippingProfile.name))
    return list(result.scalars())


@router.post("", response_model=ShippingProfileRead, status_code=status.HTTP_201_CREATED)
async def create_shipping_profile(
    payload: ShippingProfileCreate, session: AsyncSession = Depends(get_db)
) -> ShippingProfile:
    profile = ShippingProfile(**payload.model_dump())
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
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
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shipping_profile(profile_id: int, session: AsyncSession = Depends(get_db)) -> None:
    profile = await session.get(ShippingProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipping profile not found")
    await session.delete(profile)
    await session.commit()
