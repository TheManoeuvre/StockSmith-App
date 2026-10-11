"""Per-product store on/off switch (ProductPlatformSettings.is_target) and the store-level
default / one-off prompt that feed it. See docs/plan-store-product-toggles.md."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.services import catalogue_compatibility, listing_sync, product_platforms
from app.services.product_platforms import (
    apply_new_product_defaults,
    apply_store_scope,
    excluded_product_ids,
    set_product_store,
    store_states,
    target_platforms,
)

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session, name="Pot", sku="SKU-1") -> Product:
    product = Product(name=name, sku=sku)
    session.add(product)
    await session.commit()
    return product


async def _connect(session, platform, **kwargs) -> PlatformConnection:
    connection = PlatformConnection(
        platform=platform,
        access_token="a",
        refresh_token="r",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        external_account_id="1",
        **kwargs,
    )
    session.add(connection)
    await session.commit()
    return connection


async def _live(session, product, platform) -> None:
    session.add(Listing(product_id=product.id, variant_id=None, platform=platform, external_listing_id="9"))
    await session.commit()


@pytest.mark.asyncio
async def test_unset_means_on_and_explicit_off_excludes(session):
    product = await _product(session)
    await _connect(session, EBAY)
    assert await excluded_product_ids(session, EBAY) == set()

    await set_product_store(session, product.id, EBAY, False)
    await session.commit()
    assert await excluded_product_ids(session, EBAY) == {product.id}
    assert await target_platforms(session, product.id) == set()
    assert (await store_states(session))[product.id][EBAY] == (False, False)


@pytest.mark.asyncio
async def test_live_listing_pins_the_store_on(session):
    product = await _product(session)
    await _live(session, product, EBAY)
    session.add(ProductPlatformSettings(product_id=product.id, platform=EBAY, is_target=False))
    await session.commit()

    assert await excluded_product_ids(session, EBAY) == set()
    assert (await store_states(session))[product.id][EBAY] == (True, True)
    with pytest.raises(ValueError):
        await set_product_store(session, product.id, EBAY, False)


@pytest.mark.asyncio
async def test_scope_prompt_sets_everything_but_keeps_live_products_on(session):
    live = await _product(session, "Live", "SKU-L")
    other = await _product(session, "Other", "SKU-O")
    connection = await _connect(session, EBAY)
    await _live(session, live, EBAY)

    assert connection.product_scope_decided_at is None
    assert await apply_store_scope(session, EBAY, False) == 2
    await session.commit()
    await session.refresh(connection)

    assert connection.product_scope_decided_at is not None
    assert await excluded_product_ids(session, EBAY) == {other.id}
    rows = {
        r.product_id: r.is_target
        for r in (await session.execute(select(ProductPlatformSettings))).scalars()
    }
    assert rows == {live.id: True, other.id: False}


@pytest.mark.asyncio
async def test_scope_prompt_needs_a_connection_row(session):
    await _product(session)
    with pytest.raises(ValueError):
        await apply_store_scope(session, ETSY, True)


@pytest.mark.asyncio
async def test_new_products_follow_each_stores_default(session):
    await _connect(session, ETSY)
    await _connect(session, EBAY, new_products_default_on=False)
    product = await _product(session)
    await apply_new_product_defaults(session, product.id)
    await session.commit()

    assert await excluded_product_ids(session, EBAY) == {product.id}
    assert await excluded_product_ids(session, ETSY) == set()


@pytest.mark.asyncio
async def test_catalogue_scan_leaves_out_excluded_products_and_counts_them(session):
    product = await _product(session)
    await _product(session, "Other", "SKU-2")
    await _connect(session, EBAY)
    await set_product_store(session, product.id, EBAY, False)
    await session.commit()

    report = await catalogue_compatibility.scan_catalogue(session, EBAY)
    assert report.total_products == 1
    assert report.excluded_count == 1


@pytest.mark.asyncio
async def test_bulk_sku_check_skips_excluded_products(session):
    product = await _product(session)
    kept = await _product(session, "Kept", "SKU-2")
    await set_product_store(session, product.id, EBAY, False)
    await session.commit()

    result = await listing_sync.check_all_products_sku_sync(session, {}, EBAY)
    assert [s.product_id for s in result.summaries] == [kept.id]
    assert product_platforms.PUSHABLE_PLATFORMS == (ETSY, EBAY)


@pytest.mark.asyncio
async def test_products_csv_round_trips_the_store_switches(session):
    import csv
    import io

    from app.services.csv_io import export_products_csv, import_products_csv

    off = await _product(session, "Off", "SKU-OFF")
    await _product(session, "On", "SKU-ON")
    await set_product_store(session, off.id, EBAY, False)
    await session.commit()

    rows = {r["sku"]: r for r in csv.DictReader(io.StringIO(await export_products_csv(session)))}
    assert (rows["SKU-OFF"]["sold_on_ebay"], rows["SKU-OFF"]["sold_on_etsy"]) == ("false", "true")
    assert rows["SKU-ON"]["sold_on_ebay"] == "true"

    content = b"name,sku,description,is_active,sold_on_etsy,sold_on_ebay\nOff,SKU-OFF,,true,,true\nNew,SKU-NEW,,true,false,\n"
    result = await import_products_csv(session, content)
    assert result["failed"] == []
    new = (await session.execute(select(Product).where(Product.sku == "SKU-NEW"))).scalar_one()
    assert await excluded_product_ids(session, ETSY) == {new.id}  # explicit cell applied
    assert await excluded_product_ids(session, EBAY) == set()  # explicit true turned it back on


@pytest.mark.asyncio
async def test_products_csv_without_store_columns_changes_nothing(session):
    from app.services.csv_io import import_products_csv

    off = await _product(session, "Off", "SKU-OFF")
    await set_product_store(session, off.id, EBAY, False)
    await session.commit()
    await import_products_csv(session, b"name,sku,description,is_active\nOff,SKU-OFF,,true\n")
    assert await excluded_product_ids(session, EBAY) == {off.id}


@pytest.mark.asyncio
async def test_product_list_can_be_narrowed_to_a_store(session):
    from app.routers.products import list_products

    off = await _product(session, "Off", "SKU-OFF")
    on = await _product(session, "On", "SKU-ON")
    await set_product_store(session, off.id, EBAY, False)
    await session.commit()

    page = await list_products(limit=50, offset=0, sold_on=EBAY, session=session)
    assert [p.id for p in page.items] == [on.id]
    assert page.total == 1
