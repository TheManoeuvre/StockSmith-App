"""The pre-save question: would this variant request breach a target platform's limits?

Etsy caps a listing at 2 variation attributes and 100 variations; eBay at 5 and 250.
Both creation paths — generating from attributes and adding a single variant by hand —
have to ask before writing anything, and only about platforms the product actually
targets. The answer is a 409 the client turns into confirm/cancel, so "proceed" must
create exactly what "ask" would have.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.platform_connection import PlatformConnection
from app.models.platform_limits import PlatformFieldLimit
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.product import VariantAttributeSpec
from app.services import platform_limits
from app.services.platform_limits import LimitField
from app.services.variant_platform_conflicts import (
    PLATFORM_LIMIT_CONFLICTS,
    find_platform_conflicts,
    require_no_platform_conflicts,
)
from app.services.variants import generate_variants

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session) -> Product:
    product = Product(id=1, name="Widget", sku="SKU-1")
    session.add(product)
    await session.commit()
    return product


async def _connect(session, platform: ListingPlatform) -> None:
    session.add(
        PlatformConnection(
            platform=platform,
            access_token="a",
            refresh_token="r",
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_account_id="1",
        )
    )
    await session.commit()


async def _override(session, platform, field, value) -> None:
    session.add(PlatformFieldLimit(platform=platform, field_key=field, int_value=value))
    await session.commit()
    platform_limits.invalidate_limits_cache()


def _attrs(*sizes: int) -> list[VariantAttributeSpec]:
    return [
        VariantAttributeSpec(name=f"Attr{i}", values=[f"v{i}-{j}" for j in range(n)])
        for i, n in enumerate(sizes)
    ]


def _conflicts(exc) -> list[dict]:
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == PLATFORM_LIMIT_CONFLICTS
    return exc.value.detail["conflicts"]


async def _count(session) -> int:
    return len((await session.execute(select(ProductVariant))).scalars().all())


# --- the check itself ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_target_platforms_means_no_conflicts(session):
    await _product(session)
    assert await find_platform_conflicts(session, 1, attribute_count=3, active_variant_count=5000) == []


@pytest.mark.asyncio
async def test_each_platform_reports_its_own_limit(session):
    """Three attributes is fine on eBay (5) and over on Etsy (2); the message names Etsy
    and its number so the user knows which store to exclude or override."""
    await _product(session)
    await _connect(session, ETSY)
    await _connect(session, EBAY)
    conflicts = await find_platform_conflicts(session, 1, attribute_count=3)
    assert [(c["platform"], c["field"], c["resulting_count"], c["limit"]) for c in conflicts] == [
        ("etsy", "variation_attribute_max_count", 3, 2)
    ]
    assert conflicts[0]["message"] == "This will result in 3 variation attributes; Etsy supports only 2."


@pytest.mark.asyncio
async def test_variation_count_breach_on_both_platforms(session):
    await _product(session)
    await _connect(session, ETSY)
    await _connect(session, EBAY)
    conflicts = await find_platform_conflicts(session, 1, active_variant_count=300)
    assert [(c["platform"], c["limit"]) for c in conflicts] == [("ebay", 250), ("etsy", 100)]
    assert conflicts[0]["message"] == "This will result in 300 active variants on this product; eBay supports only 250."


@pytest.mark.asyncio
async def test_override_is_respected(session):
    """A shop enrolled for Etsy's third attribute raises the override; the check follows."""
    await _product(session)
    await _connect(session, ETSY)
    await _override(session, ETSY, LimitField.variation_attribute_max_count, 3)
    assert await find_platform_conflicts(session, 1, attribute_count=3) == []
    assert len(await find_platform_conflicts(session, 1, attribute_count=4)) == 1


@pytest.mark.asyncio
async def test_excluded_platform_does_not_object(session):
    await _product(session)
    await _connect(session, ETSY)
    session.add(ProductPlatformSettings(product_id=1, platform=ETSY, is_target=False))
    await session.commit()
    assert await find_platform_conflicts(session, 1, attribute_count=3) == []


@pytest.mark.asyncio
async def test_proceed_never_raises(session):
    await _product(session)
    await _connect(session, ETSY)
    await require_no_platform_conflicts(session, 1, "proceed", attribute_count=3)


# --- generation ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_asks_before_writing_attribute_names(session):
    """Nothing is persisted when the request is refused — the attribute names in
    particular, since generate_variants writes those onto the product."""
    product = await _product(session)
    await _connect(session, ETSY)
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, _attrs(2, 2, 2))
    assert [c["field"] for c in _conflicts(exc)] == ["variation_attribute_max_count"]
    assert await _count(session) == 0
    await session.refresh(product)
    assert product.variant_attribute1_name is None


@pytest.mark.asyncio
async def test_generate_counts_existing_active_variants(session):
    """The limit is on the listing, so what matters is the total after the save: 60
    existing active + 60 new is over Etsy's 100 even though neither half is."""
    await _product(session)
    await _connect(session, ETSY)
    await generate_variants(session, 1, _attrs(60))
    assert await _count(session) == 60
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, _attrs(120))
    (conflict,) = _conflicts(exc)
    assert (conflict["field"], conflict["resulting_count"]) == ("variation_max_count", 120)
    assert await _count(session) == 60


@pytest.mark.asyncio
async def test_generate_ignores_disabled_variants(session):
    await _product(session)
    await _connect(session, ETSY)
    await generate_variants(session, 1, _attrs(60))
    for variant in (await session.execute(select(ProductVariant))).scalars():
        variant.is_active = False
    await session.commit()
    created = await generate_variants(session, 1, _attrs(100))
    # 60 combos already existed (now disabled), so only the 40 new values are created;
    # 0 active + 40 is well under Etsy's 100.
    assert len(created) == 40


@pytest.mark.asyncio
async def test_generate_proceed_creates_everything(session):
    await _product(session)
    await _connect(session, ETSY)
    created = await generate_variants(session, 1, _attrs(2, 2, 2), on_platform_conflict="proceed")
    assert len(created) == 8
