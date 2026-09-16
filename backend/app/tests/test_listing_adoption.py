"""apply_adoption's only real job that isn't tested elsewhere: StockSmith's own SKU is
always what gets written as the lookup key, and a mismatch against the eBay SKU the
user picked is flagged rather than silently adopted. Driven directly against a real
session (per conftest.py's `session` fixture) since it writes Listing rows.
"""

from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import listing_adoption


async def _make_product_with_variants(session, sku: str, suffixes: list[str]) -> tuple[Product, list[ProductVariant]]:
    product = Product(name=f"Product {sku}", sku=sku, current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()

    variants = []
    for suffix in suffixes:
        variant = ProductVariant(product_id=product.id, variant_name=suffix, sku_suffix=suffix)
        session.add(variant)
        variants.append(variant)
    await session.commit()
    for v in variants:
        await session.refresh(v)
    return product, variants


async def test_adopt_no_variant_product_matching_sku_no_conflict(session):
    product, _ = await _make_product_with_variants(session, "WIDGET", [])

    result = await listing_adoption.apply_adoption(
        session,
        product,
        active_variants=[],
        variation_mapping=[(None, "WIDGET")],
        platform=ListingPlatform.ebay,
        listing_title="Widget listing",
    )

    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.sku_conflict is False
    assert unit.expected_sku == "WIDGET"
    assert unit.actual_sku == "WIDGET"

    listing = (
        await session.execute(
            Listing.__table__.select().where(Listing.product_id == product.id, Listing.platform == ListingPlatform.ebay)
        )
    ).one()
    assert listing.external_listing_id == "WIDGET"  # StockSmith's own SKU, not eBay's, is the lookup key
    assert listing.external_state == "active"


async def test_adopt_conflicting_sku_is_flagged_and_stockssmith_sku_wins(session):
    """The core source-of-truth requirement: if the eBay listing's actual SKU differs
    from what StockSmith computes, the conflict is surfaced but StockSmith's own SKU is
    still what gets persisted as the Listing's lookup key — eBay's value is never
    silently adopted."""
    product, _ = await _make_product_with_variants(session, "WIDGET", [])

    result = await listing_adoption.apply_adoption(
        session,
        product,
        active_variants=[],
        variation_mapping=[(None, "EBAY-DIFFERENT-SKU")],
        platform=ListingPlatform.ebay,
        listing_title="Widget listing",
    )

    unit = result.units[0]
    assert unit.sku_conflict is True
    assert unit.expected_sku == "WIDGET"
    assert unit.actual_sku == "EBAY-DIFFERENT-SKU"

    listing = (
        await session.execute(
            Listing.__table__.select().where(Listing.product_id == product.id, Listing.platform == ListingPlatform.ebay)
        )
    ).one()
    assert listing.external_listing_id == "WIDGET"


async def test_adopt_multi_variant_writes_one_listing_row_per_variant(session):
    """The SKU-0012 case this feature was built for: a 4-variation listing should
    produce one Listing row per StockSmith variant, each independently flagged for
    conflict."""
    product, variants = await _make_product_with_variants(session, "SKU-0012", ["A", "B", "C", "D"])
    mapping = [
        (variants[0].id, "SKU-0012-A"),
        (variants[1].id, "SKU-0012-B"),
        (variants[2].id, "SKU-0012-C"),
        (variants[3].id, "SKU-0012-DIFFERENT"),  # deliberately mismatched
    ]

    result = await listing_adoption.apply_adoption(
        session,
        product,
        active_variants=variants,
        variation_mapping=mapping,
        platform=ListingPlatform.ebay,
        listing_title="Aqara G400 mount",
    )

    assert len(result.units) == 4
    conflicts = {u.variant_id for u in result.units if u.sku_conflict}
    assert conflicts == {variants[3].id}

    listings = (
        await session.execute(
            Listing.__table__.select().where(Listing.product_id == product.id, Listing.platform == ListingPlatform.ebay)
        )
    ).all()
    assert len(listings) == 4
    by_variant = {row.variant_id: row for row in listings}
    assert by_variant[variants[0].id].external_listing_id == "SKU-0012-A"
    assert by_variant[variants[3].id].external_listing_id == "SKU-0012-D"  # StockSmith's own, not the conflicting eBay one


# --- Shipping profile: the adopted listing already ships a certain way, so a product with
# no shipping profile is pointed at the local one linked to that marketplace id.


async def _shipping_profile(session, name="Small parcel", **link):
    from decimal import Decimal

    from app.models.shipping_profile import ShippingProfile

    profile = ShippingProfile(name=name, price=Decimal("3.50"), **link)
    session.add(profile)
    await session.commit()
    return profile


async def _adopt(session, product, platform, marketplace_shipping_id):
    return await listing_adoption.apply_adoption(
        session,
        product,
        active_variants=[],
        variation_mapping=[(None, "WIDGET")],
        platform=platform,
        listing_title="Widget listing",
        external_listing_id="1234" if platform == ListingPlatform.etsy else None,
        marketplace_shipping_id=marketplace_shipping_id,
    )


async def test_adopt_sets_the_products_shipping_profile_from_the_ebay_policy_link(session):
    profile = await _shipping_profile(session, ebay_fulfillment_policy_id="policy-42")
    product, _ = await _make_product_with_variants(session, "WIDGET", [])

    result = await _adopt(session, product, ListingPlatform.ebay, "policy-42")

    assert result.shipping_profile_assigned == "Small parcel"
    assert product.shipping_profile_id == profile.id


async def test_adopt_sets_the_products_shipping_profile_from_the_etsy_link(session):
    profile = await _shipping_profile(session, etsy_shipping_profile_id=555)
    product, _ = await _make_product_with_variants(session, "WIDGET", [])

    result = await _adopt(session, product, ListingPlatform.etsy, 555)

    assert result.shipping_profile_assigned == "Small parcel"
    assert product.shipping_profile_id == profile.id


async def test_adopt_never_overwrites_a_shipping_profile_the_user_already_set(session):
    await _shipping_profile(session, ebay_fulfillment_policy_id="policy-42")
    chosen = await _shipping_profile(session, name="Large parcel")
    product, _ = await _make_product_with_variants(session, "WIDGET", [])
    product.shipping_profile_id = chosen.id
    await session.commit()

    result = await _adopt(session, product, ListingPlatform.ebay, "policy-42")

    assert result.shipping_profile_assigned is None
    assert product.shipping_profile_id == chosen.id


async def test_adopt_leaves_the_product_alone_when_no_local_profile_is_linked(session):
    product, _ = await _make_product_with_variants(session, "WIDGET", [])

    result = await _adopt(session, product, ListingPlatform.ebay, "policy-unknown")

    assert result.shipping_profile_assigned is None
    assert product.shipping_profile_id is None
