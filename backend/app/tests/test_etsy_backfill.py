"""Backfilling blank local fields from already-matched Etsy listings.

The rules being pinned are the ones that make this safe to run repeatedly without reading
every row: blanks only, per-offering prices, and one bad image never costing the rest.
"""

from decimal import Decimal

import pytest

from app.models.asset import AssetType, ProductAsset
from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import etsy_backfill, file_storage
from app.services.etsy_backfill import (
    FIELD_DESCRIPTION,
    FIELD_IMAGE,
    FIELD_PRICE,
    apply_backfill,
    build_preview,
)

# A 1x1 PNG, so thumbnail generation has something real to work on.
PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def listing(
    listing_id="900001",
    title="Brick Pencil Pot",
    description="A 3D printed pot.",
    price=None,
    products=(),
    images=(),
):
    return {
        "listing_id": int(listing_id),
        "title": title,
        "description": description,
        "price": price,
        "state": "active",
        "inventory": {"products": list(products)},
        "images": list(images),
    }


def money(amount, divisor=100, currency="GBP"):
    return {"amount": amount, "divisor": divisor, "currency_code": currency}


def offering_product(sku, amount, deleted=False):
    return {
        "sku": sku,
        "is_deleted": deleted,
        "offerings": [{"quantity": 3, "is_enabled": True, "is_deleted": False, "price": money(amount)}],
    }


async def _product(session, **kwargs) -> Product:
    defaults = dict(name="Brick Pencil Pot", sku="SKU-0037", is_active=True, description=None)
    defaults.update(kwargs)
    product = Product(**defaults)
    session.add(product)
    await session.commit()
    return product


async def _match(session, product: Product, listing_id="900001", variant_id=None) -> None:
    session.add(
        Listing(
            product_id=product.id,
            variant_id=variant_id,
            platform=ListingPlatform.etsy,
            external_listing_id=listing_id,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_proposes_a_description_for_a_product_that_has_none(session):
    product = await _product(session)
    await _match(session, product)

    preview = await build_preview(session, [listing()])
    assert len(preview.products) == 1
    assert preview.products[0].description == "A 3D printed pot."
    assert preview.products[0].description_chars == len("A 3D printed pot.")


@pytest.mark.asyncio
async def test_never_proposes_over_an_existing_value(session):
    """Fill blanks only. A disagreement is not a conflict - StockSmith's own value wins
    and stays, which is what makes this safe to re-run."""
    product = await _product(session, description="My own words", sale_price=Decimal("12.00"))
    await _match(session, product)

    preview = await build_preview(session, [listing(products=[offering_product("SKU-0037", 999)])])
    assert preview.products == []
    assert preview.already_complete == 1


@pytest.mark.asyncio
async def test_products_with_no_etsy_match_are_counted_not_proposed(session):
    await _product(session)
    preview = await build_preview(session, [listing()])
    assert preview.products == [] and preview.unmatched == 1


@pytest.mark.asyncio
async def test_ebay_matches_do_not_count_as_etsy_matches(session):
    """external_listing_id means the listing id on Etsy and the SKU on eBay, so reading an
    eBay row here would look up a listing that doesn't exist."""
    product = await _product(session)
    session.add(
        Listing(
            product_id=product.id,
            variant_id=None,
            platform=ListingPlatform.ebay,
            external_listing_id="SKU-0037",
        )
    )
    await session.commit()

    preview = await build_preview(session, [listing()])
    assert preview.unmatched == 1


@pytest.mark.asyncio
async def test_variant_prices_come_from_the_matching_sku_offering(session):
    product = await _product(session, description="x")
    session.add_all(
        [
            ProductVariant(product_id=product.id, variant_name="Blue", sku_suffix="BLUE", is_active=True),
            ProductVariant(product_id=product.id, variant_name="Teal", sku_suffix="TEAL", is_active=True),
        ]
    )
    await session.commit()
    await _match(session, product)

    preview = await build_preview(
        session,
        [
            listing(
                price=money(500),
                products=[offering_product("SKU-0037-BLUE", 1250), offering_product("SKU-0037-TEAL", 1495)],
            )
        ],
    )
    prices = {v.sku: v.proposed_price for v in preview.products[0].variant_prices}
    # Crucially not 5.00 - listing.price is documented as the *minimum* possible price, so
    # using it would silently set every variant to the cheapest one.
    assert prices == {"SKU-0037-BLUE": Decimal("12.50"), "SKU-0037-TEAL": Decimal("14.95")}


@pytest.mark.asyncio
async def test_variant_that_already_has_a_price_is_left_alone(session):
    product = await _product(session, description="x")
    session.add_all(
        [
            ProductVariant(
                product_id=product.id,
                variant_name="Blue",
                sku_suffix="BLUE",
                is_active=True,
                sale_price=Decimal("9.99"),
            ),
            ProductVariant(product_id=product.id, variant_name="Teal", sku_suffix="TEAL", is_active=True),
        ]
    )
    await session.commit()
    await _match(session, product)

    preview = await build_preview(
        session,
        [listing(products=[offering_product("SKU-0037-BLUE", 1250), offering_product("SKU-0037-TEAL", 1495)])],
    )
    assert [v.sku for v in preview.products[0].variant_prices] == ["SKU-0037-TEAL"]


@pytest.mark.asyncio
async def test_single_product_listing_falls_back_to_the_listing_price(session):
    """With no variants there is only one thing being sold, so "minimum possible price" is
    simply the price."""
    product = await _product(session, description="x")
    await _match(session, product)

    preview = await build_preview(session, [listing(price=money(1799))])
    assert preview.products[0].sale_price == Decimal("17.99")


@pytest.mark.asyncio
async def test_hero_image_is_the_lowest_ranked_one(session):
    product = await _product(session, description="x", sale_price=Decimal("1.00"))
    await _match(session, product)

    preview = await build_preview(
        session,
        [
            listing(
                images=[
                    {"rank": 3, "url_fullxfull": "https://i.etsystatic.com/c.jpg"},
                    {"rank": 1, "url_fullxfull": "https://i.etsystatic.com/a.jpg"},
                    {"rank": 2, "url_fullxfull": "https://i.etsystatic.com/b.jpg"},
                ]
            )
        ],
    )
    assert preview.products[0].image_url == "https://i.etsystatic.com/a.jpg"


@pytest.mark.asyncio
async def test_product_with_a_main_image_gets_no_image_proposal(session):
    product = await _product(session, description="x", sale_price=Decimal("1.00"))
    session.add(
        ProductAsset(
            product_id=product.id,
            asset_type=AssetType.main_image,
            file_path="p/1.jpg",
            original_filename="1.jpg",
        )
    )
    await session.commit()
    await _match(session, product)

    preview = await build_preview(
        session, [listing(images=[{"rank": 1, "url_fullxfull": "https://i.etsystatic.com/a.jpg"}])]
    )
    assert preview.products == []


@pytest.mark.asyncio
async def test_apply_writes_only_the_ticked_fields(session):
    product = await _product(session)
    await _match(session, product)
    payload = [listing(price=money(1799))]

    result = await apply_backfill(session, None, payload, {product.id: {FIELD_DESCRIPTION}})

    await session.refresh(product)
    assert result.descriptions_filled == 1 and result.prices_filled == 0
    assert product.description == "A 3D printed pot."
    assert product.sale_price is None


@pytest.mark.asyncio
async def test_apply_ignores_products_that_were_not_ticked(session):
    first = await _product(session, name="A", sku="SKU-A")
    second = await _product(session, name="B", sku="SKU-B")
    await _match(session, first, "1")
    await _match(session, second, "2")
    payload = [listing("1", description="one"), listing("2", description="two")]

    await apply_backfill(session, None, payload, {first.id: {FIELD_DESCRIPTION}})

    await session.refresh(second)
    assert second.description is None


@pytest.mark.asyncio
async def test_a_failed_image_download_does_not_lose_the_other_fields(session, monkeypatch):
    """One unreachable CDN URL should not cost the user the description it was bundled
    with, nor abort the products after it."""
    product = await _product(session)
    await _match(session, product)

    async def boom(url):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(etsy_backfill, "fetch_image_bytes", boom)

    result = await apply_backfill(
        session,
        None,
        [listing(images=[{"rank": 1, "url_fullxfull": "https://i.etsystatic.com/a.jpg"}])],
        {product.id: {FIELD_DESCRIPTION, FIELD_IMAGE}},
    )

    await session.refresh(product)
    assert product.description == "A 3D printed pot."
    assert result.images_filled == 0
    assert len(result.errors) == 1 and "connection reset" in result.errors[0]


@pytest.mark.asyncio
async def test_apply_is_idempotent(session):
    """Running it twice fills nothing the second time, because the blanks are gone."""
    product = await _product(session)
    await _match(session, product)
    payload = [listing(price=money(1799))]
    selections = {product.id: {FIELD_DESCRIPTION, FIELD_PRICE}}

    first = await apply_backfill(session, None, payload, selections)
    second = await apply_backfill(session, None, payload, selections)

    assert first.products_updated == 1
    assert second.products_updated == 0


@pytest.mark.asyncio
async def test_image_is_stored_and_linked_as_the_main_image(session, monkeypatch, tmp_path):
    product = await _product(session, description="x", sale_price=Decimal("1.00"))
    await _match(session, product)
    monkeypatch.setattr(file_storage, "asset_root", lambda: tmp_path)

    async def fake_fetch(url):
        assert url == "https://i.etsystatic.com/a.jpg"
        return PNG_1X1, "hero.png"

    monkeypatch.setattr(etsy_backfill, "fetch_image_bytes", fake_fetch)

    result = await apply_backfill(
        session,
        None,
        [listing(images=[{"rank": 1, "url_fullxfull": "https://i.etsystatic.com/a.jpg"}])],
        {product.id: {FIELD_IMAGE}},
    )

    assert result.images_filled == 1
    assert result.errors == []
