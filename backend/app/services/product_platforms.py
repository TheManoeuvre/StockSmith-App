"""Which platforms a product is *aimed at*, and which it is *already live on*.

Two questions that look like one and are not, and conflating them is where the bugs
would be:

  * **target platforms** — what a generated value has to conform to. Used by the
    constraint matrix and by anything that builds a payload.
  * **live platforms** — where a listing genuinely exists today. Used by the rule that a
    SKU already published somewhere must never be silently rewritten.

Neither existed before this module: the product page hardcoded Etsy and eBay side by
side, and "is this product on Etsy" was inferred from whether an API call 400'd.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.platform_connection import PlatformConnection
from app.services.listing_push import _PUSH_ENABLED_PLATFORMS


async def connected_platforms(session: AsyncSession) -> set[ListingPlatform]:
    """Platforms with a usable OAuth grant, intersected with the ones that can actually
    be pushed to. A connection whose adapter can't push is not a platform this product
    will ever be published on, so it has no business constraining its values."""
    result = await session.execute(select(PlatformConnection))
    return {
        connection.platform
        for connection in result.scalars()
        if connection.is_connected and connection.platform in _PUSH_ENABLED_PLATFORMS
    }


async def live_platforms(
    session: AsyncSession, product_id: int, variant_id: int | None = None
) -> set[ListingPlatform]:
    """Platforms where this product (or one specific variant) has a confirmed listing.

    Keyed on `external_listing_id` being populated rather than on the row existing at
    all. That distinction is the whole point: `listing_sync._get_or_create_listing`
    writes a row on *every* check including one that finds nothing, so a bare row means
    "someone clicked Test Sync once", not "we sell this here".
    """
    query = select(Listing).where(
        Listing.product_id == product_id, Listing.external_listing_id.is_not(None)
    )
    if variant_id is not None:
        query = query.where(Listing.variant_id == variant_id)
    result = await session.execute(query)
    return {listing.platform for listing in result.scalars()}


async def target_platforms(session: AsyncSession, product_id: int) -> set[ListingPlatform]:
    """The platforms this product's generated values must satisfy.

    Deliberately inclusive: every connected, pushable platform, plus anywhere it is
    already live even if that platform has since been disconnected. Being too broad
    costs a few characters of SKU; being too narrow means generating a 48-character SKU,
    then discovering Etsy is connected and having it rejected at push time — with the
    value already written somewhere else. The asymmetry is not close.

    A per-product `is_target` narrows it: an explicit false excludes a platform even
    though it is connected, which is how a product that cannot satisfy one marketplace's
    limits stops being constrained by them. Null means "decide from connections and live
    listings", which is every product until someone says otherwise.

    A platform the product is already live on is never excluded, whatever the flag says.
    The listing exists; pretending otherwise would let a value be generated that breaks it.
    """
    derived = await connected_platforms(session) | await live_platforms(session, product_id)

    excluded = {
        row.platform
        for row in (
            await session.execute(
                select(ProductPlatformSettings).where(
                    ProductPlatformSettings.product_id == product_id,
                    ProductPlatformSettings.is_target.is_(False),
                )
            )
        ).scalars()
    }
    live = await live_platforms(session, product_id)
    return (derived - excluded) | live


async def catalogue_target_platforms(session: AsyncSession) -> set[ListingPlatform]:
    """The union of target platforms across the whole catalogue, in two queries.

    For a shop-wide scan, resolving this per product would be one query per product for
    an answer that barely varies. Used by the compatibility report, which asks the same
    question of every product at once.
    """
    result = await session.execute(select(Listing).where(Listing.external_listing_id.is_not(None)))
    live = {listing.platform for listing in result.scalars()}
    return await connected_platforms(session) | live
