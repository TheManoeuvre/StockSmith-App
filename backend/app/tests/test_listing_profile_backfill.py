"""Deriving listing profiles from existing Etsy listings.

The premise being tested is that a shop's catalogue contains a handful of genuine metadata
combinations rather than one per product. If the grouping is too fine the feature is
useless — twenty-six proposals is not a shortcut — so most of these pin what does and
doesn't split a group.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ListingProfile, ProductPlatformSettings
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.services.listing_profile_backfill import (
    ShippingSelection,
    apply_proposals,
    apply_shipping_proposals,
    propose_profiles,
    propose_shipping_profiles,
)

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

    proposals = await propose_profiles(session, [listing(1, who_made=None)])
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
    # Shipping is no longer the listing profile's to carry: the draft takes it from the
    # product's linked ShippingProfile, proposed separately below.
    assert profile.etsy_shipping_profile_id is None
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


# --- Shipping profiles -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postage_alone_no_longer_splits_a_group(session):
    """The old signature included Etsy's shipping_profile_id, so two products that differed
    only in postage became two listing profiles. They are the same kind of listing; the
    postage now belongs to the product's own shipping profile."""
    await _matched(session, "Pot", 1)
    await _matched(session, "Big pot", 2)

    proposals = await propose_profiles(session, [listing(1, shipping_profile_id=99), listing(2, shipping_profile_id=100)])
    assert len(proposals) == 1
    assert proposals[0].is_complete is True


@pytest.mark.asyncio
async def test_each_unlinked_etsy_shipping_profile_is_proposed_once(session):
    await _matched(session, "Pot", 1)
    await _matched(session, "Big pot", 2)
    await _matched(session, "Keychain", 3)

    etsy_profiles = [
        {"id": 99, "title": "Small parcel", "profile_type": "manual", "domestic_price": Decimal("3.60")},
        {"id": 100, "title": "Large parcel", "profile_type": "manual", "domestic_price": Decimal("5.20")},
    ]
    proposals = await propose_shipping_profiles(
        session,
        [listing(1, shipping_profile_id=99), listing(2, shipping_profile_id=100), listing(3, shipping_profile_id=99)],
        etsy_profiles,
    )
    assert [(p.etsy_shipping_profile_id, p.title, len(p.product_ids)) for p in proposals] == [
        (99, "Small parcel", 2),
        (100, "Large parcel", 1),
    ]
    assert proposals[0].domestic_price == Decimal("3.60")


@pytest.mark.asyncio
async def test_an_etsy_profile_already_linked_locally_is_not_proposed(session):
    await _matched(session, "Pot", 1)
    session.add(ShippingProfile(name="Small parcel", price=Decimal("3.60"), etsy_shipping_profile_id=99))
    await session.commit()

    assert await propose_shipping_profiles(session, [listing(1, shipping_profile_id=99)]) == []


@pytest.mark.asyncio
async def test_accepting_creates_a_linked_profile_priced_from_etsy_and_assigns_products(session):
    pot = await _matched(session, "Pot", 1)
    etsy_profiles = [{"id": 99, "title": "Small parcel", "profile_type": "manual", "domestic_price": Decimal("3.60")}]

    result = await apply_shipping_proposals(
        session, [listing(1, shipping_profile_id=99)], [ShippingSelection(etsy_shipping_profile_id=99)], etsy_profiles=etsy_profiles
    )

    assert result.shipping_profiles_created == 1 and result.shipping_products_assigned == 1
    profile = (await session.execute(select(ShippingProfile))).scalar_one()
    assert profile.name == "Small parcel"
    assert profile.etsy_shipping_profile_id == 99
    assert Decimal(profile.price_etsy) == Decimal("3.60")
    # A new profile with £0 default postage would flatter every manual margin, so the Etsy
    # figure seeds the default too. Costs stay zero — Etsy knows nothing about them.
    assert Decimal(profile.price) == Decimal("3.60")
    assert Decimal(profile.cost_etsy) == 0
    await session.refresh(pot)
    assert pot.shipping_profile_id == profile.id


@pytest.mark.asyncio
async def test_accepting_can_link_an_existing_profile_instead(session):
    pot = await _matched(session, "Pot", 1)
    existing = ShippingProfile(name="Small parcel 48", price=Decimal("3.00"))
    session.add(existing)
    await session.commit()

    result = await apply_shipping_proposals(
        session,
        [listing(1, shipping_profile_id=99)],
        [ShippingSelection(etsy_shipping_profile_id=99, link_shipping_profile_id=existing.id)],
        etsy_profiles=[{"id": 99, "title": "Small parcel", "profile_type": "manual", "domestic_price": Decimal("3.60")}],
    )

    assert result.shipping_profiles_linked == 1 and result.shipping_profiles_created == 0
    await session.refresh(existing)
    assert existing.etsy_shipping_profile_id == 99
    assert Decimal(existing.price_etsy) == Decimal("3.60")
    # Linking imports the Etsy price into the Etsy column only; the manual figure is the user's.
    assert Decimal(existing.price) == Decimal("3.00")
    await session.refresh(pot)
    assert pot.shipping_profile_id == existing.id


@pytest.mark.asyncio
async def test_a_product_with_its_own_shipping_profile_is_never_reassigned(session):
    pot = await _matched(session, "Pot", 1)
    own = ShippingProfile(name="Own choice", price=Decimal("1"))
    session.add(own)
    await session.commit()
    pot.shipping_profile_id = own.id
    await session.commit()

    await apply_shipping_proposals(session, [listing(1, shipping_profile_id=99)], [ShippingSelection(etsy_shipping_profile_id=99)])

    await session.refresh(pot)
    assert pot.shipping_profile_id == own.id


@pytest.mark.asyncio
async def test_a_calculated_etsy_profile_is_created_without_a_price(session):
    await _matched(session, "Pot", 1)
    await apply_shipping_proposals(
        session,
        [listing(1, shipping_profile_id=99)],
        [ShippingSelection(etsy_shipping_profile_id=99)],
        etsy_profiles=[{"id": 99, "title": "Calculated", "profile_type": "calculated", "domestic_price": None}],
    )
    profile = (await session.execute(select(ShippingProfile))).scalar_one()
    assert profile.price_etsy is None and Decimal(profile.price) == 0
