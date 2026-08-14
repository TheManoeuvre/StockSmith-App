"""Target vs live platform resolution.

The distinction these two functions draw is the whole point of the module: "which
platforms must this value satisfy" and "where is this product already published" are
different questions, and answering the first with the second's data is how a value gets
generated against the wrong constraints.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.services.product_platforms import (
    catalogue_target_platforms,
    connected_platforms,
    live_platforms,
    target_platforms,
)

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session, **kwargs) -> Product:
    product = Product(name="Brick Pencil Pot", sku="SKU-0037", **kwargs)
    session.add(product)
    await session.commit()
    return product


async def _connect(session, platform: ListingPlatform, *, connected: bool = True) -> None:
    session.add(
        PlatformConnection(
            platform=platform,
            access_token="a",
            refresh_token="r" if connected else None,
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_account_id="1",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_no_connections_means_no_targets(session):
    product = await _product(session)
    assert await target_platforms(session, product.id) == set()


@pytest.mark.asyncio
async def test_connected_platform_becomes_a_target(session):
    product = await _product(session)
    await _connect(session, ETSY)
    assert await target_platforms(session, product.id) == {ETSY}


@pytest.mark.asyncio
async def test_a_connection_without_a_refresh_token_is_not_connected(session):
    """PlatformConnection.is_connected keys on the refresh token — a disconnected row is
    left in place with its tokens nulled, so row existence proves nothing."""
    product = await _product(session)
    await _connect(session, ETSY, connected=False)
    assert await connected_platforms(session) == set()
    assert await target_platforms(session, product.id) == set()


@pytest.mark.asyncio
async def test_listing_row_from_a_failed_check_does_not_make_a_platform_live(session):
    """listing_sync._get_or_create_listing writes a row on every check including a miss.
    Treating that as "we sell here" would target every platform anyone ever clicked Test
    Sync against."""
    product = await _product(session)
    session.add(Listing(product_id=product.id, variant_id=None, platform=EBAY))
    await session.commit()

    assert await live_platforms(session, product.id) == set()
    assert await target_platforms(session, product.id) == set()


@pytest.mark.asyncio
async def test_matched_listing_makes_a_platform_live(session):
    product = await _product(session)
    session.add(
        Listing(product_id=product.id, variant_id=None, platform=EBAY, external_listing_id="12345")
    )
    await session.commit()
    assert await live_platforms(session, product.id) == {EBAY}


@pytest.mark.asyncio
async def test_live_platform_targets_even_when_disconnected(session):
    """Deliberately inclusive. A product live on eBay still has to satisfy eBay's limits
    even if the connection has since been dropped — the listing is still out there."""
    product = await _product(session)
    await _connect(session, ETSY)
    session.add(
        Listing(product_id=product.id, variant_id=None, platform=EBAY, external_listing_id="12345")
    )
    await session.commit()
    assert await target_platforms(session, product.id) == {ETSY, EBAY}


@pytest.mark.asyncio
async def test_live_platforms_can_be_scoped_to_one_variant(session):
    product = await _product(session)
    session.add_all(
        [
            Listing(product_id=product.id, variant_id=None, platform=ETSY, external_listing_id="1"),
            Listing(product_id=product.id, variant_id=None, platform=EBAY),
        ]
    )
    await session.commit()
    assert await live_platforms(session, product.id) == {ETSY}


@pytest.mark.asyncio
async def test_catalogue_targets_union_across_products(session):
    """The shop-wide scan resolves this once rather than per product — same answer, two
    queries instead of one per row."""
    first = await _product(session)
    second = Product(name="Bike Keychain", sku="SKU-0029")
    session.add(second)
    await session.commit()

    await _connect(session, ETSY)
    session.add(
        Listing(product_id=second.id, variant_id=None, platform=EBAY, external_listing_id="9")
    )
    await session.commit()

    assert await catalogue_target_platforms(session) == {ETSY, EBAY}
    assert await target_platforms(session, first.id) == {ETSY}
