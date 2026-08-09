"""Deleting, archiving and merging shipping profiles.

Shipping profiles are reference data with one thing the others don't have: orders point at them.
An order shipped under a profile is a record of what happened, not a preference — so the
operations that are fine for a manufacturer are not fine here, and these tests pin the
difference.
"""

from datetime import datetime, timezone

import pytest

from app.models.order import Order
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.services import reference_data
from app.services.reference_data import InUseError


async def _profile(session, name: str, **kwargs) -> ShippingProfile:
    profile = ShippingProfile(name=name, price=3, cost_etsy=2, cost_ebay=2, cost_manual=2, **kwargs)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def _order(session, profile_id: int | None) -> Order:
    order = Order(
        platform="etsy",
        external_order_id=f"R{profile_id}-{datetime.now(timezone.utc).timestamp()}",
        order_placed_at=datetime.now(timezone.utc),
        shipping_profile_id=profile_id,
    )
    session.add(order)
    await session.commit()
    return order


async def _product(session, name: str, profile_id: int | None) -> Product:
    product = Product(name=name, sku=name.lower().replace(" ", "-"), shipping_profile_id=profile_id)
    session.add(product)
    await session.commit()
    return product


class TestDelete:
    async def test_deletes_a_profile_nothing_uses(self, session):
        profile = await _profile(session, "Never used")
        await reference_data.delete_if_unused(session, ShippingProfile, profile.id)
        assert await session.get(ShippingProfile, profile.id) is None

    async def test_refuses_while_a_product_uses_it(self, session):
        profile = await _profile(session, "Small parcel")
        await _product(session, "Doorbell Mount", profile.id)

        with pytest.raises(InUseError, match="1 product"):
            await reference_data.delete_if_unused(session, ShippingProfile, profile.id)

    async def test_refuses_while_a_historical_order_references_it(self, session):
        """The behaviour this phase exists to fix.

        Delete used to be unconditional, and every FK here is ON DELETE SET NULL — so removing a
        profile silently changed what a shipped order says it was shipped under. Quietly
        rewriting history, reported as 204.
        """
        profile = await _profile(session, "Retired courier")
        await _order(session, profile.id)

        with pytest.raises(InUseError, match="1 order"):
            await reference_data.delete_if_unused(session, ShippingProfile, profile.id)

        assert await session.get(ShippingProfile, profile.id) is not None

    async def test_counts_every_kind_of_reference(self, session):
        profile = await _profile(session, "Busy")
        await _product(session, "A", profile.id)
        await _order(session, profile.id)

        assert await reference_data.describe_usage(session, ShippingProfile, profile.id) == (
            "1 product and 1 order"
        )


class TestMerge:
    async def test_folds_a_duplicate_used_only_by_products(self, session):
        keep = await _profile(session, "Small parcel")
        dupe = await _profile(session, "small parcel")
        product = await _product(session, "Doorbell Mount", dupe.id)

        target = await reference_data.merge(session, ShippingProfile, dupe.id, keep.id)

        assert target.id == keep.id
        await session.refresh(product)
        assert product.shipping_profile_id == keep.id
        assert await session.get(ShippingProfile, dupe.id) is None

    async def test_refuses_when_an_order_references_the_source(self, session):
        """Merging is the right cure for a duplicate and the wrong one for history — repointing
        an order's profile changes what that order records."""
        keep = await _profile(session, "Small parcel")
        dupe = await _profile(session, "small parcel")
        await _order(session, dupe.id)

        with pytest.raises(InUseError, match="archive it instead"):
            await reference_data.merge(session, ShippingProfile, dupe.id, keep.id)

        assert await session.get(ShippingProfile, dupe.id) is not None


class TestArchive:
    async def test_archiving_keeps_existing_references_resolving(self, session):
        """The whole point: retire it from the pickers without touching what already points at
        it. An order shipped under this profile still knows its costs."""
        profile = await _profile(session, "Retired courier")
        await _order(session, profile.id)

        profile.is_archived = True
        await session.commit()
        await session.refresh(profile)

        assert profile.is_archived is True
        assert float(profile.cost_etsy) == 2.0
        counts = await reference_data.usage_counts(session, ShippingProfile)
        assert counts[profile.id] == 1

    async def test_profiles_start_unarchived(self, session):
        profile = await _profile(session, "Standard")
        assert profile.is_archived is False
