"""The whole-catalogue conformance scan.

The behaviour worth pinning is what the report *doesn't* say: a clean catalogue produces
an empty product list rather than 26 entries saying "fine", and a violation is attributed
to the platform being asked about rather than to whichever platform happens to be
strictest overall.
"""

import pytest

from app.models.asset import AssetType, ProductAsset
from app.models.listing import ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.catalogue_compatibility import scan_catalogue
from app.services.platform_limits import LimitField, Severity

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _product(session, **kwargs) -> Product:
    defaults = dict(name="Brick Pencil Pot", sku="SKU-0037", is_active=True)
    defaults.update(kwargs)
    product = Product(**defaults)
    session.add(product)
    await session.commit()
    return product


async def _variant(session, product: Product, **kwargs) -> ProductVariant:
    defaults = dict(product_id=product.id, variant_name="Blue", sku_suffix="BLUE", is_active=True)
    defaults.update(kwargs)
    variant = ProductVariant(**defaults)
    session.add(variant)
    await session.commit()
    return variant


@pytest.mark.asyncio
async def test_clean_catalogue_reports_nothing_but_still_counts_products(session):
    await _product(session)
    report = await scan_catalogue(session, ETSY)
    assert report.total_products == 1
    assert report.blocked_count == 0 and report.warning_count == 0
    assert report.products == []


@pytest.mark.asyncio
async def test_empty_catalogue_is_not_an_error(session):
    report = await scan_catalogue(session, ETSY)
    assert report.total_products == 0 and report.products == []


@pytest.mark.asyncio
async def test_inactive_products_are_ignored(session):
    await _product(session, name="X" * 500, is_active=False)
    report = await scan_catalogue(session, EBAY)
    assert report.total_products == 0 and report.products == []


@pytest.mark.asyncio
async def test_over_length_sku_is_reported_on_the_unit_not_the_product(session):
    product = await _product(session, sku="SKU-0037")
    await _variant(session, product, sku_suffix="A" * 40)

    report = await scan_catalogue(session, ETSY)
    assert report.blocked_count == 1
    entry = report.products[0]
    assert entry.violations == []
    assert len(entry.units) == 1
    assert entry.units[0].violations[0].field is LimitField.sku_max_length
    assert entry.units[0].violations[0].severity is Severity.blocker


@pytest.mark.asyncio
async def test_only_units_with_findings_are_returned(session):
    """A 76-variation product with one bad SKU should surface that one variant, not 76
    rows the user has to search through."""
    product = await _product(session)
    await _variant(session, product, variant_name="Fine", sku_suffix="OK")
    await _variant(session, product, variant_name="Bad", sku_suffix="B" * 40)

    report = await scan_catalogue(session, ETSY)
    units = report.products[0].units
    assert [u.variant_name for u in units] == ["Bad"]


@pytest.mark.asyncio
async def test_report_is_scoped_to_the_platform_asked_about(session):
    """A 100-character title breaches eBay's 80 but not Etsy's 140. Asking about Etsy must
    not surface eBay's limit, or the user cannot tell which store needs action."""
    await _product(session, name="T" * 100)

    assert await scan_catalogue(session, ETSY) == await scan_catalogue(session, ETSY)
    assert (await scan_catalogue(session, ETSY)).products == []

    ebay = await scan_catalogue(session, EBAY)
    assert ebay.warning_count == 1
    violation = ebay.products[0].violations[0]
    assert violation.field is LimitField.title_max_length
    assert violation.imposed_by is EBAY


@pytest.mark.asyncio
async def test_three_attributes_block_on_etsy_only(session):
    await _product(
        session,
        variant_attribute1_name="Size",
        variant_attribute2_name="Colour",
        variant_attribute3_name="Finish",
    )
    etsy = await scan_catalogue(session, ETSY)
    assert etsy.blocked_count == 1
    assert etsy.products[0].is_blocked is True
    assert (await scan_catalogue(session, EBAY)).products == []


@pytest.mark.asyncio
async def test_a_warning_alone_does_not_mark_a_product_blocked(session):
    await _product(session, name="T" * 100)
    report = await scan_catalogue(session, EBAY)
    assert report.products[0].is_blocked is False
    assert report.blocked_count == 0 and report.warning_count == 1


@pytest.mark.asyncio
async def test_blocked_products_sort_above_warnings(session):
    """The list is a worklist: what stops a listing existing belongs at the top, even when
    its name sorts later."""
    warned = await _product(session, name="Z" * 100, sku="SKU-A")
    blocked = await _product(session, name="Apple", sku="SKU-B")
    await _variant(session, blocked, sku_suffix="B" * 60)

    report = await scan_catalogue(session, EBAY)
    assert [p.product_id for p in report.products] == [blocked.id, warned.id]


@pytest.mark.asyncio
async def test_image_count_over_the_cap_is_flagged(session):
    product = await _product(session)
    for i in range(21):
        session.add(
            ProductAsset(
                product_id=product.id,
                asset_type=AssetType.listing_image,
                file_path=f"p/{i}.jpg",
                original_filename=f"{i}.jpg",
            )
        )
    await session.commit()

    report = await scan_catalogue(session, ETSY)
    assert LimitField.image_max_count in [v.field for v in report.products[0].violations]


@pytest.mark.asyncio
async def test_non_image_assets_do_not_count_towards_the_image_cap(session):
    product = await _product(session)
    for i in range(25):
        session.add(
            ProductAsset(
                product_id=product.id,
                asset_type=AssetType.gcode,
                file_path=f"p/{i}.gcode",
                original_filename=f"{i}.gcode",
            )
        )
    await session.commit()
    assert (await scan_catalogue(session, ETSY)).products == []
