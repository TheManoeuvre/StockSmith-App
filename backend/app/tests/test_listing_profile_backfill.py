"""Deriving listing profiles from existing Etsy listings.

The premise being tested is that a shop's catalogue contains a handful of genuine metadata
combinations rather than one per product. If the grouping is too fine the feature is
useless — twenty-six proposals is not a shortcut — so most of these pin what does and
doesn't split a group.
"""

import pytest
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ListingProfile, ProductPlatformSettings
from app.models.product import Product
from app.services.listing_profile_backfill import apply_proposals, propose_profiles

ETSY = ListingPlatform.etsy


def listing(listing_id, **overrides):
    base = {
        "listing_id": int(listing_id),
        "title": "A listing",
        "taxonomy_id": 1234,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "is_supply": False,
        "shipping_profile_id": 99,
        "return_policy_id": 7,
        "processing_min": 1,
        "processing_max": 3,
        "shop_section_id": 42,
    }
    base.update(overrides)
    return base


async def _matched(session, name, listing_id) -> Product:
    product = Product(name=name, sku=f"SKU-{listing_id}", is_active=True)
    session.add(product)
    await session.commit()
    session.add(
        Listing(
            product_id=product.id,
            variant_id=None,
            platform=ETSY,
            external_listing_id=str(listing_id),
        )
    )
    await session.commit()
    return product


@pytest.mark.asyncio
async def test_identical_listings_collapse_into_one_proposal(session):
    await _matched(session, "Pot", 1)
    await _matched(session, "Keychain", 2)

    proposals = await propose_profiles(session, [listing(1), listing(2)])
    assert len(proposals) == 1
    assert len(proposals[0].product_ids) == 2
    assert proposals[0].is_complete is True


@pytest.mark.asyncio
async def test_a_different_category_makes_a_separate_proposal(session):
    await _matched(session, "Pot", 1)
    await _matched(session, "Vintage thing", 2)

    proposals = await propose_profiles(session, [listing(1), listing(2, taxonomy_id=5678)])
    assert len(proposals) == 2


@pytest.mark.asyncio
async def test_processing_times_do_not_split_a_group(session):
    """These vary listing to listing without changing what kind of thing is being sold.
    Grouping on them would shatter three real profiles into fifteen."""
    await _matched(session, "Pot", 1)
    await _matched(session, "Keychain", 2)

    proposals = await propose_profiles(
        session, [listing(1, processing_min=1, processing_max=3), listing(2, processing_min=5, processing_max=9)]
    )
    assert len(proposals) == 1


@pytest.mark.asyncio
async def test_biggest_group_comes_first(session):
    """The largest group is the one that should become the default, so it belongs at the
    top where accepting it is the obvious move."""
    for i in (1, 2, 3):
        await _matched(session, f"Common {i}", i)
    await _matched(session, "Odd one", 9)

    proposals = await propose_profiles(
        session, [listing(1), listing(2), listing(3), listing(9, taxonomy_id=5678)]
    )
    assert [len(p.product_ids) for p in proposals] == [3, 1]


@pytest.mark.asyncio
async def test_unmatched_products_contribute_nothing(session):
    product = Product(name="Never listed", sku="SKU-X", is_active=True)
    session.add(product)
    await session.commit()

    assert await propose_profiles(session, [listing(1)]) == []


@pytest.mark.asyncio
async def test_a_listing_with_no_metadata_at_all_is_skipped(session):
    """It would otherwise group every such product under an empty proposal that can't be
    used to create anything."""
    await _matched(session, "Bare", 1)

    empty = listing(
        1,
        taxonomy_id=None,
        who_made=None,
        when_made=None,
        is_supply=None,
        shipping_profile_id=None,
        return_policy_id=None,
    )
    assert await propose_profiles(session, [empty]) == []


@pytest.mark.asyncio
async def test_an_incomplete_combination_is_still_proposed_but_flagged(session):
    """Seeing that eleven products share an incomplete combination is how you learn which
    single field to go and set."""
    await _matched(session, "Pot", 1)

    proposals = await propose_profiles(session, [listing(1, shipping_profile_id=None)])
    assert len(proposals) == 1
    assert proposals[0].is_complete is False


@pytest.mark.asyncio
async def test_suggested_name_leads_with_the_making_details(session):
    """Etsy's taxonomy id is a number nobody recognises on sight."""
    await _matched(session, "Pot", 1)
    proposals = await propose_profiles(session, [listing(1)])
    assert proposals[0].suggested_name.startswith("Handmade")


@pytest.mark.asyncio
async def test_apply_creates_the_profile_and_assigns_its_products(session):
    pot = await _matched(session, "Pot", 1)
    keychain = await _matched(session, "Keychain", 2)

    result = await apply_proposals(session, [listing(1), listing(2)], {0: "3D printed home"})

    assert result.profiles_created == 1 and result.products_assigned == 2
    profile = (await session.execute(select(ListingProfile))).scalar_one()
    assert profile.name == "3D printed home"
    assert profile.etsy_taxonomy_id == 1234
    assert profile.etsy_who_made == "i_did"
    assert profile.etsy_shipping_profile_id == 99
    # Carried through even though it doesn't define the group.
    assert profile.etsy_processing_min == 1

    settings = (await session.execute(select(ProductPlatformSettings))).scalars().all()
    assert {s.product_id for s in settings} == {pot.id, keychain.id}
    assert all(s.listing_profile_id == profile.id for s in settings)


@pytest.mark.asyncio
async def test_the_first_profile_created_becomes_the_default(session):
    await _matched(session, "Pot", 1)
    await apply_proposals(session, [listing(1)], {0: "Handmade"})
    profile = (await session.execute(select(ListingProfile))).scalar_one()
    assert profile.is_default is True


@pytest.mark.asyncio
async def test_only_the_accepted_proposals_are_created(session):
    await _matched(session, "Pot", 1)
    await _matched(session, "Vintage", 2)

    result = await apply_proposals(session, [listing(1), listing(2, taxonomy_id=5678)], {0: "Kept"})

    assert result.profiles_created == 1
    names = [p.name for p in (await session.execute(select(ListingProfile))).scalars()]
    assert names == ["Kept"]


@pytest.mark.asyncio
async def test_products_can_be_left_unassigned(session):
    await _matched(session, "Pot", 1)
    result = await apply_proposals(session, [listing(1)], {0: "Handmade"}, assign_products=False)

    assert result.profiles_created == 1 and result.products_assigned == 0
    assert (await session.execute(select(ProductPlatformSettings))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_out_of_range_selection_is_ignored(session):
    """The index refers to a position in a freshly re-derived list, so a stale preview
    could name one that no longer exists."""
    await _matched(session, "Pot", 1)
    result = await apply_proposals(session, [listing(1)], {7: "Nonexistent"})
    assert result.profiles_created == 0


@pytest.mark.asyncio
async def test_an_empty_name_falls_back_to_the_suggestion(session):
    await _matched(session, "Pot", 1)
    await apply_proposals(session, [listing(1)], {0: "   "})
    profile = (await session.execute(select(ListingProfile))).scalar_one()
    assert profile.name.startswith("Handmade")
