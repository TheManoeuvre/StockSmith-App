"""Listing profiles: the marketplace metadata a listing needs, as named reusable bundles.

A product uses the profile chosen for it on that platform, or none. There is no platform
default to fall back to: the profile fixes the category, processing time and policies the
listing goes out with, so it has to be a choice someone made for this product rather than
whichever profile happened to be flagged. "None" is a real outcome and is handled by the
caller as a blocker, not papered over with invented values — see the module docstring on
ListingProfile for why guessing a policy id is worse than refusing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile, ProductPlatformSettings


async def list_profiles(session: AsyncSession, platform: ListingPlatform) -> list[ListingProfile]:
    result = await session.execute(
        select(ListingProfile)
        .where(ListingProfile.platform == platform)
        .order_by(ListingProfile.name)
    )
    return list(result.scalars())


async def resolve_profile(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> ListingProfile | None:
    """The profile chosen for this product on this platform, or None if none has been."""
    settings = (
        await session.execute(
            select(ProductPlatformSettings).where(
                ProductPlatformSettings.product_id == product_id,
                ProductPlatformSettings.platform == platform,
            )
        )
    ).scalar_one_or_none()

    if settings is None or settings.listing_profile_id is None:
        return None
    # The FK is ON DELETE SET NULL, so a dangling id only happens mid-transaction, and None
    # is the same outcome the deletion produces anyway.
    return await session.get(ListingProfile, settings.listing_profile_id)


async def get_or_create_settings(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> ProductPlatformSettings:
    settings = (
        await session.execute(
            select(ProductPlatformSettings).where(
                ProductPlatformSettings.product_id == product_id,
                ProductPlatformSettings.platform == platform,
            )
        )
    ).scalar_one_or_none()
    if settings is None:
        settings = ProductPlatformSettings(product_id=product_id, platform=platform)
        session.add(settings)
    return settings
