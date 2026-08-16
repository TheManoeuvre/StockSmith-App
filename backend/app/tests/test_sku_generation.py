"""Numeric SKU generation, and the rule that a live SKU never changes.

Two properties carry the whole design. Codes are *allocated*, not positional — so deleting
a value cannot renumber the ones after it and silently rewrite a SKU that is already
printed on a listing. And a product never mixes the two schemes, so adding one colour to an
existing product doesn't produce a sibling that looks nothing like the rest.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.attribute_value_code import ProductAttributeValueCode
from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.product import VariantAttributeSpec
from app.services import sku_generation
from app.services.product_platforms import target_platforms
from app.services.sku_generation import (
    build_suffixes,
    is_numeric_suffix,
    live_skus_elsewhere,
    scheme_for,
)
from app.services.variants import compute_full_sku, generate_variants

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session, **kwargs) -> Product:
    defaults = dict(name="Brick Pencil Pot", sku="SKU-0037", is_active=True)
    defaults.update(kwargs)
    product = Product(**defaults)
    session.add(product)
    await session.commit()
    return product


def _spec(name, values):
    return VariantAttributeSpec(name=name, values=values)


# --- scheme selection ---


def test_a_product_with_no_variants_is_new_and_gets_numeric():
    assert scheme_for([]) == "numeric"


def test_a_product_with_readable_suffixes_keeps_them():
    """Adding a colour to an existing product must not produce a numeric sibling — a
    product half in each scheme is worse at the bench than either scheme alone."""
    existing = [ProductVariant(product_id=1, variant_name="Blue", sku_suffix="4-STUD-BLUE")]
    assert scheme_for(existing) == "readable"


def test_a_product_already_numeric_stays_numeric():
    existing = [ProductVariant(product_id=1, variant_name="Blue", sku_suffix="01-02")]
    assert scheme_for(existing) == "numeric"


def test_variants_with_no_suffix_do_not_decide_the_scheme():
    existing = [ProductVariant(product_id=1, variant_name="Only", sku_suffix=None)]
    assert scheme_for(existing) == "numeric"


def test_recognises_which_suffixes_are_numeric():
    assert is_numeric_suffix("01-02-03")
    assert is_numeric_suffix("07")
    assert not is_numeric_suffix("4-STUD-BLUE")
    # A value that happens to start with a digit is still readable.
    assert not is_numeric_suffix("4-STUD")
    assert not is_numeric_suffix(None)


# --- generation ---


@pytest.mark.asyncio
async def test_a_new_product_generates_short_numeric_skus(session):
    product = await _product(session)
    variants = await generate_variants(
        session,
        product.id,
        [_spec("Studs", ["4 Stud", "6 Stud"]), _spec("Colour", ["Sunflower Yellow", "Teal"])],
    )

    suffixes = sorted(v.sku_suffix for v in variants)
    assert suffixes == ["01-01", "01-02", "02-01", "02-02"]
    # The readable equivalent of the longest of these is 32 characters, exactly Etsy's cap.
    assert all(len(compute_full_sku(product.sku, v.sku_suffix)) == 14 for v in variants)


@pytest.mark.asyncio
async def test_length_does_not_grow_with_longer_attribute_values(session):
    """The whole point: length depends on how many attributes there are, not what they are
    called."""
    short = await _product(session, name="A", sku="SKU-A")
    long = await _product(session, name="B", sku="SKU-B")

    a = await generate_variants(session, short.id, [_spec("Size", ["S"])])
    b = await generate_variants(
        session, long.id, [_spec("Colour", ["Absolutely Enormous Sunflower Yellow Metallic"])]
    )
    assert len(a[0].sku_suffix) == len(b[0].sku_suffix)


@pytest.mark.asyncio
async def test_adding_a_value_reuses_existing_codes_and_leaves_old_skus_alone(session):
    product = await _product(session)
    first = await generate_variants(session, product.id, [_spec("Colour", ["Blue", "Teal"])])
    original = {v.variant_name: v.sku_suffix for v in first}

    await generate_variants(session, product.id, [_spec("Colour", ["Blue", "Teal", "Pink"])])

    rows = (
        await session.execute(select(ProductVariant).where(ProductVariant.product_id == product.id))
    ).scalars().all()
    by_name = {v.variant_name: v.sku_suffix for v in rows}

    # Existing SKUs are untouched, and the new value gets a fresh code rather than
    # displacing anything.
    assert by_name["Blue"] == original["Blue"]
    assert by_name["Teal"] == original["Teal"]
    assert by_name["Pink"] == "03"


@pytest.mark.asyncio
async def test_a_deleted_value_does_not_renumber_the_others(session):
    """The reason codes are stored rather than derived from list position. A positional
    code would shift every value after the deleted one, rewriting SKUs that are live."""
    product = await _product(session)
    await generate_variants(session, product.id, [_spec("Colour", ["Blue", "Teal", "Pink"])])

    codes = {
        r.value: r.code
        for r in (
            await session.execute(
                select(ProductAttributeValueCode).where(
                    ProductAttributeValueCode.product_id == product.id
                )
            )
        ).scalars()
    }
    assert codes == {"Blue": 1, "Teal": 2, "Pink": 3}

    # Drop the middle value and generate again without it.
    teal = (
        await session.execute(
            select(ProductVariant).where(
                ProductVariant.product_id == product.id, ProductVariant.attribute1_value == "Teal"
            )
        )
    ).scalar_one()
    await session.delete(teal)
    await session.commit()

    await generate_variants(session, product.id, [_spec("Colour", ["Blue", "Pink", "Green"])])

    codes = {
        r.value: r.code
        for r in (
            await session.execute(
                select(ProductAttributeValueCode).where(
                    ProductAttributeValueCode.product_id == product.id
                )
            )
        ).scalars()
    }
    # Pink keeps 3, and Green takes 4 rather than reusing Teal's retired 2 — that code may
    # still be printed on a listing somewhere.
    assert codes["Blue"] == 1
    assert codes["Pink"] == 3
    assert codes["Green"] == 4


@pytest.mark.asyncio
async def test_an_existing_readable_product_keeps_generating_readable_suffixes(session):
    """The 275 SKUs already in the live catalogue are readable. Adding a colour to one of
    those products must not start a second scheme inside it."""
    product = await _product(session)
    session.add(
        ProductVariant(
            product_id=product.id,
            variant_name="Blue",
            sku_suffix="BLUE",
            attribute1_value="Blue",
            is_active=True,
        )
    )
    await session.commit()

    created = await generate_variants(session, product.id, [_spec("Colour", ["Blue", "Teal"])])
    assert [v.sku_suffix for v in created] == ["TEAL"]


@pytest.mark.asyncio
async def test_codes_are_scoped_to_one_product(session):
    """Per-product rather than global: both marketplaces show the variation names beside
    the SKU, so it doesn't have to be self-describing on its own."""
    a = await _product(session, name="A", sku="SKU-A")
    b = await _product(session, name="B", sku="SKU-B")

    await generate_variants(session, a.id, [_spec("Colour", ["Teal"])])
    await generate_variants(session, b.id, [_spec("Colour", ["Blue"])])

    rows = (await session.execute(select(ProductAttributeValueCode))).scalars().all()
    assert {(r.product_id, r.value, r.code) for r in rows} == {(a.id, "Teal", 1), (b.id, "Blue", 1)}


@pytest.mark.asyncio
async def test_build_suffixes_allocates_once_for_the_whole_batch(session):
    product = await _product(session)
    combos = [("Blue", "S"), ("Blue", "M"), ("Teal", "S")]
    suffixes = await build_suffixes(session, product.id, [], combos)

    assert suffixes[("Blue", "S")] == "01-01"
    assert suffixes[("Blue", "M")] == "01-02"
    assert suffixes[("Teal", "S")] == "02-01"


# --- live SKU protection ---


@pytest.mark.asyncio
async def test_a_sync_match_records_what_the_marketplace_confirmed(session):
    from app.services.listing_sync import check_product_sku_sync
    from app.services.platforms.base import ExternalListingRef

    product = await _product(session)
    index = {
        "SKU-0037": ExternalListingRef(
            external_listing_id="900001", title="Pot", sku="SKU-0037", state="active",
            quantity=3, variation=None,
        )
    }
    await check_product_sku_sync(session, product.id, index, ETSY)

    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.published_sku == "SKU-0037"


@pytest.mark.asyncio
async def test_a_miss_does_not_erase_a_previously_confirmed_sku(session):
    """A listing that has gone inactive is still published under that SKU. Forgetting it
    would let a later rename change an identifier still in use out there."""
    from app.services.listing_sync import check_product_sku_sync
    from app.services.platforms.base import ExternalListingRef

    product = await _product(session)
    ref = ExternalListingRef(
        external_listing_id="900001", title="Pot", sku="SKU-0037", state="active",
        quantity=3, variation=None,
    )
    await check_product_sku_sync(session, product.id, {"SKU-0037": ref}, ETSY)
    await check_product_sku_sync(session, product.id, {}, ETSY)

    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.external_listing_id is None  # the match is gone...
    assert listing.published_sku == "SKU-0037"  # ...but what was published is remembered


@pytest.mark.asyncio
async def test_live_skus_are_reported_per_platform(session):
    product = await _product(session)
    session.add_all(
        [
            Listing(product_id=product.id, variant_id=None, platform=ETSY, published_sku="SKU-0037"),
            Listing(product_id=product.id, variant_id=None, platform=EBAY, published_sku="SKU-0037-OLD"),
        ]
    )
    await session.commit()

    assert await live_skus_elsewhere(session, product.id, None) == {
        ETSY: "SKU-0037",
        EBAY: "SKU-0037-OLD",
    }
    # Excluding the platform being changed answers "what would this break elsewhere?"
    assert await live_skus_elsewhere(session, product.id, None, excluding=ETSY) == {
        EBAY: "SKU-0037-OLD"
    }


@pytest.mark.asyncio
async def test_a_listing_never_confirmed_is_not_reported_as_live(session):
    product = await _product(session)
    session.add(Listing(product_id=product.id, variant_id=None, platform=ETSY))
    await session.commit()
    assert await live_skus_elsewhere(session, product.id, None) == {}


# --- per-product platform opt-out ---


async def _connect(session, platform):
    from datetime import datetime, timedelta, timezone

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


@pytest.mark.asyncio
async def test_an_explicit_opt_out_removes_a_connected_platform(session):
    product = await _product(session)
    await _connect(session, ETSY)
    await _connect(session, EBAY)
    session.add(ProductPlatformSettings(product_id=product.id, platform=ETSY, is_target=False))
    await session.commit()

    assert await target_platforms(session, product.id) == {EBAY}


@pytest.mark.asyncio
async def test_null_means_decide_from_connections(session):
    product = await _product(session)
    await _connect(session, ETSY)
    session.add(ProductPlatformSettings(product_id=product.id, platform=ETSY, is_target=None))
    await session.commit()

    assert await target_platforms(session, product.id) == {ETSY}


@pytest.mark.asyncio
async def test_opting_out_cannot_drop_a_platform_the_product_is_live_on(session):
    """The listing exists. Pretending otherwise would let a value be generated that breaks
    it, which is exactly the failure the opt-out is meant to avoid elsewhere."""
    product = await _product(session)
    await _connect(session, ETSY)
    session.add_all(
        [
            ProductPlatformSettings(product_id=product.id, platform=ETSY, is_target=False),
            Listing(
                product_id=product.id, variant_id=None, platform=ETSY, external_listing_id="900001"
            ),
        ]
    )
    await session.commit()

    assert await target_platforms(session, product.id) == {ETSY}
