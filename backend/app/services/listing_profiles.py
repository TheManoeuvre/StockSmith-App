"""Listing profiles: the marketplace metadata a listing needs, as named reusable bundles.

Resolution for a product is `its own profile ?? the platform's default profile ?? none`.
"None" is a real outcome and is handled by the caller as a blocker, not papered over with
invented values — see the module docstring on ListingProfile for why guessing a policy id
is worse than refusing.
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
        # Default first, then alphabetical: the default is the one most answers will use,
        # so it belongs at the top of every picker.
        .order_by(ListingProfile.is_default.desc(), ListingProfile.name)
    )
    return list(result.scalars())


async def get_default_profile(session: AsyncSession, platform: ListingPlatform) -> ListingProfile | None:
    return (
        await session.execute(
            select(ListingProfile).where(
                ListingProfile.platform == platform, ListingProfile.is_default.is_(True)
            )
        )
    ).scalar_one_or_none()


async def promote_to_default(session: AsyncSession, platform: ListingPlatform, profile: ListingProfile) -> None:
    """Makes this the platform's default, demoting whichever profile held it.

    "There can only be one default" is a rule to apply, not a constraint error to hand
    back: the user asked for this one, so the other gets demoted rather than the request
    being refused.

    Order is load-bearing. A partial unique index allows a single default per platform, so
    the old one has to be demoted *and flushed* before the new one is set — assigning
    first and demoting second lets autoflush write two defaults into the same index and
    fail with an IntegrityError the caller cannot do anything useful with.

    For the same reason `profile.is_default` must still be false when this is called; the
    query below would otherwise autoflush it. Callers set it through this function, not
    before it.
    """
    for other in await list_profiles(session, platform):
        if other.is_default and other.id != profile.id:
            other.is_default = False
    await session.flush()
    profile.is_default = True


async def resolve_profile(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> ListingProfile | None:
    """The profile this product uses on this platform, or None if nothing applies."""
    settings = (
        await session.execute(
            select(ProductPlatformSettings).where(
                ProductPlatformSettings.product_id == product_id,
                ProductPlatformSettings.platform == platform,
            )
        )
    ).scalar_one_or_none()

    if settings is not None and settings.listing_profile_id is not None:
        profile = await session.get(ListingProfile, settings.listing_profile_id)
        if profile is not None:
            return profile
        # The FK is ON DELETE SET NULL, so this only happens mid-transaction. Falling
        # through to the default is the same outcome the deletion produces anyway.

    return await get_default_profile(session, platform)


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
