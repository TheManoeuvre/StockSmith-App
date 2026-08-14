"""Per-platform listing configuration: field-limit overrides.

A separate router rather than another section of fee_config.py, which already owns
currency, forecasting, the default kitting BOM, margin config and fee components. Adding
listing configuration would make it six unrelated concerns behind one prefix.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.platform_limits import PlatformFieldLimit
from app.schemas.listing_profile import (
    ListingProfileCreate,
    ListingProfileRead,
    ListingProfileUpdate,
)
from app.schemas.platform_config import PlatformFieldLimitRead, PlatformFieldLimitWrite
from app.services import listing_profiles, platform_limits
from app.services.platform_limits import LimitField

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])

_LABELS = {
    LimitField.sku_max_length: "SKU length",
    LimitField.title_max_length: "Title length",
    LimitField.title_charset: "Title characters",
    LimitField.description_max_length: "Description length",
    LimitField.variation_attribute_max_count: "Variation attributes",
    LimitField.variation_max_count: "Variations per listing",
    LimitField.attribute_name_max_length: "Attribute name length",
    LimitField.attribute_value_max_length: "Attribute value length",
    LimitField.attribute_value_charset: "Attribute value characters",
    LimitField.image_max_count: "Images per listing",
    LimitField.price_decimal_places: "Price decimal places",
    LimitField.quantity_max: "Maximum quantity",
}


def _require_supported(platform: ListingPlatform) -> None:
    if platform not in platform_limits.supported_platforms():
        # Shopify is in the enum but has no adapter and no limits, so there is nothing
        # here to edit and an override would constrain products it can never list.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{platform.value} has no listing limits to configure",
        )


@router.get("/platform-limits/{platform}", response_model=list[PlatformFieldLimitRead])
async def list_platform_limits(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> list[PlatformFieldLimitRead]:
    """Every limit for this platform, with its shipped default and any override."""
    _require_supported(platform)

    defaults = platform_limits.default_limits(platform)
    overrides = {
        row.field_key: row
        for row in (
            await session.execute(
                select(PlatformFieldLimit).where(PlatformFieldLimit.platform == platform)
            )
        ).scalars()
    }

    result: list[PlatformFieldLimitRead] = []
    for field, default in defaults.items():
        override = overrides.get(field)
        override_value = None
        if override is not None:
            override_value = override.int_value if override.int_value is not None else override.text_value
        result.append(
            PlatformFieldLimitRead(
                field=field,
                label=_LABELS[field],
                kind="int" if isinstance(default.value, int) else "text",
                default_value=str(default.value),
                override_value=None if override_value is None else str(override_value),
                effective_value=str(override_value if override_value is not None else default.value),
                is_override=override_value is not None,
                note=override.note if override else None,
            )
        )
    return result


@router.put("/platform-limits/{platform}/{field}", response_model=PlatformFieldLimitRead)
async def set_platform_limit(
    platform: ListingPlatform,
    field: LimitField,
    payload: PlatformFieldLimitWrite,
    session: AsyncSession = Depends(get_db),
) -> PlatformFieldLimitRead:
    _require_supported(platform)
    defaults = platform_limits.default_limits(platform)
    default = defaults.get(field)
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{platform.value} has no {field.value} limit to override",
        )

    if payload.int_value is None and payload.text_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="An override needs a value"
        )
    # A numeric limit stored as text would break the resolver's smallest-wins comparison,
    # so the type has to match the default it replaces rather than being trusted from the
    # request.
    if isinstance(default.value, int) and payload.int_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{_LABELS[field]} needs a number"
        )
    if not isinstance(default.value, int) and payload.text_value is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{_LABELS[field]} needs a text rule"
        )

    existing = (
        await session.execute(
            select(PlatformFieldLimit).where(
                PlatformFieldLimit.platform == platform, PlatformFieldLimit.field_key == field
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = PlatformFieldLimit(platform=platform, field_key=field)
        session.add(existing)
    existing.int_value = payload.int_value
    existing.text_value = payload.text_value
    existing.note = payload.note
    await session.commit()

    # The compatibility report has to reflect this on the very next request, not after a
    # restart — the same reasoning as invalidating the adapter cache after a credential
    # write.
    platform_limits.invalidate_limits_cache()

    value = payload.int_value if payload.int_value is not None else payload.text_value
    return PlatformFieldLimitRead(
        field=field,
        label=_LABELS[field],
        kind="int" if isinstance(default.value, int) else "text",
        default_value=str(default.value),
        override_value=str(value),
        effective_value=str(value),
        is_override=True,
        note=payload.note,
    )


@router.delete("/platform-limits/{platform}/{field}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_platform_limit(
    platform: ListingPlatform, field: LimitField, session: AsyncSession = Depends(get_db)
) -> None:
    """Drops the override so the shipped default applies again — including any correction
    that arrived in a later release while the override was masking it."""
    existing = (
        await session.execute(
            select(PlatformFieldLimit).where(
                PlatformFieldLimit.platform == platform, PlatformFieldLimit.field_key == field
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No override to clear")
    await session.delete(existing)
    await session.commit()
    platform_limits.invalidate_limits_cache()


# --- Listing profiles -------------------------------------------------------------


@router.get("/listing-profiles/{platform}", response_model=list[ListingProfileRead])
async def list_listing_profiles(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> list[ListingProfile]:
    return await listing_profiles.list_profiles(session, platform)


@router.post(
    "/listing-profiles/{platform}", response_model=ListingProfileRead, status_code=status.HTTP_201_CREATED
)
async def create_listing_profile(
    platform: ListingPlatform, payload: ListingProfileCreate, session: AsyncSession = Depends(get_db)
) -> ListingProfile:
    _require_supported(platform)
    fields = payload.model_dump()
    # Created as a non-default and promoted afterwards: promote_to_default has to demote
    # the incumbent before this row claims the flag, or the partial unique index rejects
    # the flush.
    wants_default = fields.pop("is_default", False)
    profile = ListingProfile(platform=platform, is_default=False, **fields)
    session.add(profile)
    await session.flush()

    # The first profile for a platform becomes its default whether or not it asked to be.
    # Otherwise every product would report "no listing profile applies" while one plainly
    # exists.
    if wants_default or await listing_profiles.get_default_profile(session, platform) is None:
        await listing_profiles.promote_to_default(session, platform, profile)
    await session.commit()
    await session.refresh(profile)
    return profile


@router.patch("/listing-profiles/{platform}/{profile_id}", response_model=ListingProfileRead)
async def update_listing_profile(
    platform: ListingPlatform,
    profile_id: int,
    payload: ListingProfileUpdate,
    session: AsyncSession = Depends(get_db),
) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.platform != platform:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing profile not found")
    fields = payload.model_dump(exclude_unset=True)
    # Held back and applied through promote_to_default for the ordering reason described
    # there; setting it inline would let the next query autoflush two defaults at once.
    wants_default = fields.pop("is_default", None)
    for field, value in fields.items():
        setattr(profile, field, value)
    if wants_default:
        await listing_profiles.promote_to_default(session, platform, profile)
    elif wants_default is False:
        profile.is_default = False
    await session.commit()
    await session.refresh(profile)
    return profile


@router.delete("/listing-profiles/{platform}/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_listing_profile(
    platform: ListingPlatform, profile_id: int, session: AsyncSession = Depends(get_db)
) -> None:
    """Products using this profile fall back to the platform default — the FK is
    ON DELETE SET NULL, so their settings and listing copy survive."""
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.platform != platform:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing profile not found")
    await session.delete(profile)
    await session.commit()
