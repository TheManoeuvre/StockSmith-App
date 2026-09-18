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


async def _profile(session, platform=ETSY, name="Handmade", **kwargs) -> ListingProfile:
    profile = ListingProfile(platform=platform, name=name, **kwargs)
    session.add(profile)
    await session.commit()
    return profile


# --- profile resolution ---


@pytest.mark.asyncio
async def test_no_profiles_at_all_resolves_to_none(session):
    product = await _product(session)
    assert await listing_profiles.resolve_profile(session, product.id, ETSY) is None


@pytest.mark.asyncio
async def test_an_unchosen_profile_is_not_applied(session):
    """There is no platform default: a profile existing is not the same as it having been
    picked for this product, and the readiness check turns None into a blocker."""
    product = await _product(session)
    await _profile(session, name="Handmade")
    assert await listing_profiles.resolve_profile(session, product.id, ETSY) is None


@pytest.mark.asyncio
async def test_a_products_chosen_profile_is_used(session):
    product = await _product(session)
    await _profile(session, name="Handmade")
    special = await _profile(session, name="Vintage")
    session.add(
        ProductPlatformSettings(product_id=product.id, platform=ETSY, listing_profile_id=special.id)
    )
    await session.commit()

    assert (await listing_profiles.resolve_profile(session, product.id, ETSY)).id == special.id


@pytest.mark.asyncio
async def test_profiles_do_not_leak_across_platforms(session):
    product = await _product(session)
    etsy_only = await _profile(session, platform=ETSY, name="Etsy handmade")
    session.add(
        ProductPlatformSettings(product_id=product.id, platform=ETSY, listing_profile_id=etsy_only.id)
    )
    await session.commit()
    assert await listing_profiles.resolve_profile(session, product.id, EBAY) is None


@pytest.mark.asyncio
async def test_profiles_are_listed_alphabetically(session):
    await _profile(session, name="Zzz")
    await _profile(session, name="Aaa")
    names = [p.name for p in await listing_profiles.list_profiles(session, ETSY)]
    assert names == ["Aaa", "Zzz"]


@pytest.mark.asyncio
async def test_deleting_a_profile_leaves_the_products_settings_intact(session):
    """ON DELETE SET NULL: the product is left with no profile rather than losing its
    listing copy along with it."""
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

