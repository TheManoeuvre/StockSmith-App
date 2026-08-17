"""Listing profiles and the listing-copy fallback chain.

The profile design exists so a product that differs from the norm picks a different
bundle rather than overriding six fields one at a time — which is what makes an
incoherent half-overridden combination unrepresentable. These tests pin the resolution
order that delivers that.
"""

import pytest

from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile, ProductPlatformSettings
from app.models.product import Product
from app.services import listing_profiles
from app.services.listing_copy import resolve_copy

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session, **kwargs) -> Product:
    defaults = dict(name="Brick Pencil Pot", sku="SKU-0037", is_active=True)
    defaults.update(kwargs)
    product = Product(**defaults)
    session.add(product)
    await session.commit()
    return product


async def _profile(session, platform=ETSY, name="Handmade", is_default=False, **kwargs) -> ListingProfile:
    profile = ListingProfile(platform=platform, name=name, is_default=is_default, **kwargs)
    session.add(profile)
    await session.commit()
    return profile


# --- profile resolution ---


@pytest.mark.asyncio
async def test_no_profiles_at_all_resolves_to_none(session):
    product = await _product(session)
    assert await listing_profiles.resolve_profile(session, product.id, ETSY) is None


@pytest.mark.asyncio
async def test_falls_back_to_the_platform_default(session):
    product = await _product(session)
    default = await _profile(session, is_default=True)
    assert (await listing_profiles.resolve_profile(session, product.id, ETSY)).id == default.id


@pytest.mark.asyncio
async def test_a_products_own_profile_beats_the_default(session):
    product = await _product(session)
    await _profile(session, name="Default", is_default=True)
    special = await _profile(session, name="Vintage")
    session.add(
        ProductPlatformSettings(product_id=product.id, platform=ETSY, listing_profile_id=special.id)
    )
    await session.commit()

    assert (await listing_profiles.resolve_profile(session, product.id, ETSY)).id == special.id


@pytest.mark.asyncio
async def test_profiles_do_not_leak_across_platforms(session):
    product = await _product(session)
    await _profile(session, platform=ETSY, name="Etsy default", is_default=True)
    assert await listing_profiles.resolve_profile(session, product.id, EBAY) is None


@pytest.mark.asyncio
async def test_marking_a_new_default_demotes_the_old_one(session):
    """A partial unique index allows only one default per platform, so this has to be
    applied as a rule rather than surfaced as a constraint error the user can't act on."""
    first = await _profile(session, name="First", is_default=True)
    second = await _profile(session, name="Second")

    await listing_profiles.promote_to_default(session, ETSY, second)
    await session.commit()

    await session.refresh(first)
    assert first.is_default is False
    assert (await listing_profiles.get_default_profile(session, ETSY)).id == second.id


@pytest.mark.asyncio
async def test_default_profile_is_listed_first(session):
    await _profile(session, name="Aaa")
    await _profile(session, name="Zzz", is_default=True)
    names = [p.name for p in await listing_profiles.list_profiles(session, ETSY)]
    assert names == ["Zzz", "Aaa"]


@pytest.mark.asyncio
async def test_deleting_a_profile_leaves_the_products_settings_intact(session):
    """ON DELETE SET NULL: the product drops back to the platform default rather than
    losing its listing copy along with the profile."""
    product = await _product(session)
    profile = await _profile(session, name="Doomed")
    session.add(
        ProductPlatformSettings(
            product_id=product.id,
            platform=ETSY,
            listing_profile_id=profile.id,
            listing_title="Handmade Brick Pot",
        )
    )
    await session.commit()

    from sqlalchemy import select

    settings = (
        await session.execute(select(ProductPlatformSettings).where(ProductPlatformSettings.product_id == product.id))
    ).scalar_one()

    await session.delete(profile)
    await session.commit()
    # SQLite applies ON DELETE SET NULL in the database, so the in-memory row still holds
    # the old id until it is re-read.
    await session.refresh(settings)

    assert settings.listing_profile_id is None
    assert settings.listing_title == "Handmade Brick Pot"


# --- listing copy resolution ---


def _settings(**kwargs) -> ProductPlatformSettings:
    return ProductPlatformSettings(product_id=1, platform=ETSY, **kwargs)


def test_copy_falls_back_to_the_inventory_name_and_description():
    """Nothing breaks for a product nobody has written listing copy for, which is every
    product on the day this ships."""
    product = Product(name="Brick Pencil Pot", description="A pot.")
    copy = resolve_copy(product, None)
    assert copy.title == "Brick Pencil Pot"
    assert copy.title_source == "product_name"
    assert copy.description == "A pot."


def test_shared_listing_copy_beats_the_inventory_name():
    product = Product(
        name="Brick Pencil Pot",
        description="A pot.",
        listing_title="Brick Pencil Pot | 3D Printed Desk Tidy",
        listing_description="Long SEO copy.",
    )
    copy = resolve_copy(product, None)
    assert copy.title == "Brick Pencil Pot | 3D Printed Desk Tidy"
    assert copy.title_source == "shared"
    assert copy.description_source == "shared"


def test_platform_copy_beats_the_shared_copy():
    """Etsy allows 140 title characters and eBay 80, so one shared title cannot serve both
    at the top end — this is the level that exists for that."""
    product = Product(name="Brick Pencil Pot", listing_title="A long Etsy-shaped title")
    copy = resolve_copy(product, _settings(listing_title="Short eBay title"))
    assert copy.title == "Short eBay title"
    assert copy.title_source == "platform"


def test_blank_copy_is_treated_as_absent_not_as_an_empty_title():
    """Clearing the box means "use the shared copy", not "publish a blank title"."""
    product = Product(name="Brick Pencil Pot", listing_title="Shared title")
    copy = resolve_copy(product, _settings(listing_title="   "))
    assert copy.title == "Shared title"


def test_description_resolves_independently_of_title():
    product = Product(name="Brick Pencil Pot", description="A pot.", listing_title="Shared title")
    copy = resolve_copy(product, _settings(listing_description="Etsy-specific body"))
    assert copy.title == "Shared title"
    assert copy.description == "Etsy-specific body"
    assert copy.description_source == "platform"


def test_missing_description_everywhere_resolves_to_none():
    copy = resolve_copy(Product(name="Brick Pencil Pot"), None)
    assert copy.description is None
    assert copy.description_source == "missing"


# --- router paths for the default flag ---
#
# Driven through the endpoint functions rather than the service, because the ordering bug
# these cover lived in the router: it set is_default before demoting the incumbent, and
# the next query autoflushed two defaults into a partial unique index that permits one.


@pytest.mark.asyncio
async def test_first_profile_becomes_the_default_without_being_asked(session):
    from app.routers.platform_config import create_listing_profile
    from app.schemas.listing_profile import ListingProfileCreate

    created = await create_listing_profile(ETSY, ListingProfileCreate(name="Handmade"), session)
    assert created.is_default is True


@pytest.mark.asyncio
async def test_creating_a_second_default_demotes_the_first(session):
    from app.routers.platform_config import create_listing_profile
    from app.schemas.listing_profile import ListingProfileCreate

    first = await create_listing_profile(ETSY, ListingProfileCreate(name="Handmade"), session)
    second = await create_listing_profile(
        ETSY, ListingProfileCreate(name="Vintage", is_default=True), session
    )

    await session.refresh(first)
    assert first.is_default is False
    assert second.is_default is True


@pytest.mark.asyncio
async def test_promoting_an_existing_profile_via_patch_demotes_the_incumbent(session):
    from app.routers.platform_config import create_listing_profile, update_listing_profile
    from app.schemas.listing_profile import ListingProfileCreate, ListingProfileUpdate

    first = await create_listing_profile(ETSY, ListingProfileCreate(name="Handmade"), session)
    second = await create_listing_profile(ETSY, ListingProfileCreate(name="Vintage"), session)

    updated = await update_listing_profile(
        ETSY, second.id, ListingProfileUpdate(is_default=True), session
    )

    await session.refresh(first)
    assert updated.is_default is True
    assert first.is_default is False


@pytest.mark.asyncio
async def test_patching_another_field_leaves_the_default_flag_alone(session):
    from app.routers.platform_config import create_listing_profile, update_listing_profile
    from app.schemas.listing_profile import ListingProfileCreate, ListingProfileUpdate

    profile = await create_listing_profile(ETSY, ListingProfileCreate(name="Handmade"), session)
    updated = await update_listing_profile(
        ETSY, profile.id, ListingProfileUpdate(etsy_taxonomy_id=1234), session
    )
    assert updated.is_default is True
    assert updated.etsy_taxonomy_id == 1234
