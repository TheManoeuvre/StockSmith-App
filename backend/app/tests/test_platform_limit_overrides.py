"""Sparse limit overrides.

The property that matters most is the one this table exists for: because only overrides
are stored, a corrected default shipped in a later release still reaches an install that
overrode some *other* field. Seeding the table with defaults would have broken that
silently, which is exactly what happened to platform_fee_components.
"""

import pytest

from app.models.listing import ListingPlatform
from app.models.platform_limits import PlatformFieldLimit
from app.models.product import Product
from app.services import platform_limits
from app.services.catalogue_compatibility import scan_catalogue
from app.services.platform_limits import (
    LimitField,
    default_limits,
    load_limit_table,
    resolve_effective_limits,
)

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _override(session, platform, field, *, int_value=None, text_value=None, note=None):
    session.add(
        PlatformFieldLimit(
            platform=platform, field_key=field, int_value=int_value, text_value=text_value, note=note
        )
    )
    await session.commit()
    platform_limits.invalidate_limits_cache()


@pytest.mark.asyncio
async def test_table_ships_empty_and_resolves_to_defaults(session):
    table = await load_limit_table(session)
    assert table[ETSY][LimitField.sku_max_length].value == 32
    assert table[ETSY][LimitField.sku_max_length].is_override is False


@pytest.mark.asyncio
async def test_an_override_replaces_one_field_and_is_marked_as_such(session):
    await _override(session, ETSY, LimitField.sku_max_length, int_value=45, note="confirmed live")

    table = await load_limit_table(session)
    limit = table[ETSY][LimitField.sku_max_length]
    assert limit.value == 45
    assert limit.is_override is True


@pytest.mark.asyncio
async def test_overriding_one_field_leaves_every_other_default_intact(session):
    """The whole point of storing only overrides: a corrected default in a later release
    still reaches an install that overrode something else."""
    await _override(session, ETSY, LimitField.sku_max_length, int_value=45)

    table = await load_limit_table(session)
    assert table[ETSY][LimitField.title_max_length].value == 140
    assert table[ETSY][LimitField.title_max_length].is_override is False
    assert table[EBAY][LimitField.sku_max_length].value == 50


@pytest.mark.asyncio
async def test_clearing_an_override_restores_the_shipped_default(session):
    await _override(session, ETSY, LimitField.sku_max_length, int_value=45)
    row = (await session.execute(__import__("sqlalchemy").select(PlatformFieldLimit))).scalar_one()
    await session.delete(row)
    await session.commit()
    platform_limits.invalidate_limits_cache()

    table = await load_limit_table(session)
    assert table[ETSY][LimitField.sku_max_length].value == 32


@pytest.mark.asyncio
async def test_overrides_feed_strictest_wins_resolution(session):
    """Raising Etsy's SKU cap past eBay's should hand the crown to eBay."""
    await _override(session, ETSY, LimitField.sku_max_length, int_value=64)
    table = await load_limit_table(session)

    effective = resolve_effective_limits({ETSY, EBAY}, table)
    assert effective[LimitField.sku_max_length].value == 50
    assert effective[LimitField.sku_max_length].platform is EBAY


@pytest.mark.asyncio
async def test_third_variation_becomes_allowed_by_data_not_code(session):
    """The first real use of this table: Etsy's third variation ships capped at 2 and is
    raised once the shop is enrolled, with no release involved."""
    product = Product(
        name="Brick Pencil Pot",
        sku="SKU-0037",
        is_active=True,
        variant_attribute1_name="Studs",
        variant_attribute2_name="Colour",
        variant_attribute3_name="Finish",
    )
    session.add(product)
    await session.commit()

    assert (await scan_catalogue(session, ETSY)).blocked_count == 1

    await _override(session, ETSY, LimitField.variation_attribute_max_count, int_value=3)
    assert (await scan_catalogue(session, ETSY)).products == []


@pytest.mark.asyncio
async def test_a_charset_rule_can_be_overridden_as_text(session):
    await _override(session, ETSY, LimitField.attribute_value_charset, text_value="deny:[<>]")
    table = await load_limit_table(session)
    assert table[ETSY][LimitField.attribute_value_charset].value == "deny:[<>]"


@pytest.mark.asyncio
async def test_override_for_a_platform_with_no_defaults_is_ignored(session):
    """Shopify has no adapter and no limits. A lone override would let it constrain
    products it can never list."""
    await _override(session, ListingPlatform.shopify, LimitField.sku_max_length, int_value=10)
    table = await load_limit_table(session)
    assert ListingPlatform.shopify not in table


@pytest.mark.asyncio
async def test_pure_resolver_still_ignores_the_database(session):
    """resolve_effective_limits with no table stays a pure function of the shipped
    defaults — that is what keeps the matrix unit-testable without a database."""
    await _override(session, ETSY, LimitField.sku_max_length, int_value=45)
    assert resolve_effective_limits({ETSY})[LimitField.sku_max_length].value == 32
    assert default_limits(ETSY)[LimitField.sku_max_length].value == 32
