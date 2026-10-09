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

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.services import listing_profiles
from app.services.listing_push import _PUSH_ENABLED_PLATFORMS


# The platforms a product can be switched on or off for — the ones that carry listings.
PUSHABLE_PLATFORMS = _PUSH_ENABLED_PLATFORMS


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


# --- Per-product store switch ------------------------------------------------------------
#
# `ProductPlatformSettings.is_target` is the switch. Null is "on" (what every product did
# before the switch existed), so nothing changes until someone turns a store off. A live
# listing overrides an explicit false, for the same reason target_platforms does.


async def excluded_product_ids(session: AsyncSession, platform: ListingPlatform) -> set[int]:
    """Products switched off for `platform` — in one pass, for scans and list screens.

    A product with a confirmed listing there is never excluded, whatever its flag says.
    """
    off = {
        row[0]
        for row in await session.execute(
            select(ProductPlatformSettings.product_id).where(
                ProductPlatformSettings.platform == platform,
                ProductPlatformSettings.is_target.is_(False),
            )
        )
    }
    if not off:
        return off
    live = {
        row[0]
        for row in await session.execute(
            select(Listing.product_id).where(
                Listing.platform == platform,
                Listing.external_listing_id.is_not(None),
                Listing.product_id.in_(off),
            )
        )
    }
    return off - live


async def store_states(
    session: AsyncSession, product_ids: list[int] | None = None
) -> dict[int, dict[ListingPlatform, tuple[bool, bool]]]:
    """Per product, per push-enabled platform: (is_on, is_locked).

    Locked means a live listing pins it on. Only products with something to say are keyed
    explicitly; callers treat a missing product or platform as (True, False).
    """
    settings_query = select(
        ProductPlatformSettings.product_id, ProductPlatformSettings.platform, ProductPlatformSettings.is_target
    ).where(ProductPlatformSettings.is_target.is_not(None))
    live_query = select(Listing.product_id, Listing.platform).where(Listing.external_listing_id.is_not(None))
    if product_ids is not None:
        settings_query = settings_query.where(ProductPlatformSettings.product_id.in_(product_ids))
        live_query = live_query.where(Listing.product_id.in_(product_ids))

    live = {(pid, platform) for pid, platform in await session.execute(live_query)}
    states: dict[int, dict[ListingPlatform, tuple[bool, bool]]] = {}
    for pid, platform, is_target in await session.execute(settings_query):
        if platform not in _PUSH_ENABLED_PLATFORMS:
            continue
        locked = (pid, platform) in live
        states.setdefault(pid, {})[platform] = (True if locked else bool(is_target), locked)
    for pid, platform in live:
        if platform in _PUSH_ENABLED_PLATFORMS:
            states.setdefault(pid, {}).setdefault(platform, (True, True))
    return states


async def set_product_store(
    session: AsyncSession, product_id: int, platform: ListingPlatform, on: bool
) -> ProductPlatformSettings:
    """Turns one product on or off for one store.

    Refuses to switch off a product that is live there: it would keep selling out from
    under a store StockSmith had stopped looking at.
    """
    if not on and platform in await live_platforms(session, product_id):
        raise ValueError(f"Product is live on {platform.value}; end the listing there before turning the store off")
    settings = await listing_profiles.get_or_create_settings(session, product_id, platform)
    settings.is_target = on
    return settings


async def apply_store_scope(session: AsyncSession, platform: ListingPlatform, on: bool) -> int:
    """Answers the one-off prompt: sets every product on or off for `platform`.

    Products live on the platform stay on. Stamps product_scope_decided_at so the prompt
    is never raised again. Returns how many products were set.
    """
    connection = (
        await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    ).scalar_one_or_none()
    if connection is None:
        raise ValueError(f"{platform.value} has never been connected")

    product_ids = [row[0] for row in await session.execute(select(Product.id))]
    live = {
        row[0]
        for row in await session.execute(
            select(Listing.product_id).where(Listing.platform == platform, Listing.external_listing_id.is_not(None))
        )
    }
    existing = {
        s.product_id: s
        for s in (
            await session.execute(select(ProductPlatformSettings).where(ProductPlatformSettings.platform == platform))
        ).scalars()
    }
    for pid in product_ids:
        value = True if pid in live else on
        row = existing.get(pid)
        if row is None:
            session.add(ProductPlatformSettings(product_id=pid, platform=platform, is_target=value))
        else:
            row.is_target = value
    connection.product_scope_decided_at = datetime.now(timezone.utc)
    return len(product_ids)


async def apply_new_product_defaults(session: AsyncSession, product_id: int) -> None:
    """Creates the explicit "off" rows for a new product on stores whose default is off.

    Stores defaulting to on write nothing: unset already means on, and an explicit row
    would add nothing but rows.
    """
    result = await session.execute(
        select(PlatformConnection).where(PlatformConnection.new_products_default_on.is_(False))
    )
    for connection in result.scalars():
        if connection.platform in _PUSH_ENABLED_PLATFORMS:
            session.add(ProductPlatformSettings(product_id=product_id, platform=connection.platform, is_target=False))
