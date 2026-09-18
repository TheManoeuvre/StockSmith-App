"""Whether a draft listing could be created, and what's missing if not.

The distinction being pinned throughout is blocker vs warning. A blocker is something the
marketplace refuses outright; a warning is something the user should see but which
doesn't stop the draft. Getting that boundary wrong in either direction is expensive —
too strict and nothing can ever be drafted, too loose and the create call 400s after the
user has committed to it.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.asset import AssetType, ProductAsset
from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile, ProductPlatformSettings
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.draft_readiness import BLOCKER, WARNING, evaluate

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _complete_etsy_profile(session, **overrides) -> ListingProfile:
    fields = dict(
        platform=ETSY,
        name="Handmade",
        etsy_taxonomy_id=1234,
        etsy_who_made="i_did",
        etsy_when_made="made_to_order",
        etsy_is_supply=False,
        etsy_shipping_profile_id=99,
        etsy_readiness_state_id=7,
    )
    fields.update(overrides)
    profile = ListingProfile(**fields)
    session.add(profile)
    await session.commit()
    return profile


async def _ready_product(session, *, profile: ListingProfile | None = None, **overrides) -> Product:
    """A product pointed at `profile`, or — since nothing applies a profile by itself,
    there being no platform default — at whichever single profile the test has made."""
    fields = dict(
        name="Brick Pencil Pot",
        sku="SKU-0037",
        description="A 3D printed desk tidy.",
        sale_price=Decimal("12.50"),
        is_active=True,
    )
    fields.update(overrides)
    product = Product(**fields)
    session.add(product)
    await session.flush()
    if profile is None:
        profile = (await session.execute(select(ListingProfile))).scalars().first()
    if profile is not None:
        session.add(
            ProductPlatformSettings(product_id=product.id, platform=profile.platform, listing_profile_id=profile.id)
        )
    await session.commit()
    return product


def _fields(report, severity=None):
    return [i.field for i in report.issues if severity is None or i.severity == severity]


@pytest.mark.asyncio
async def test_unknown_product_returns_none(session):
    assert await evaluate(session, 999, ETSY) is None


@pytest.mark.asyncio
async def test_a_fully_prepared_product_can_be_drafted(session):
    await _complete_etsy_profile(session)
    product = await _ready_product(session)
    session.add(
        ProductAsset(
            product_id=product.id,
            asset_type=AssetType.main_image,
            file_path="p/1.jpg",
            original_filename="1.jpg",
        )
    )
    await session.commit()

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is True
    assert report.issues == []


@pytest.mark.asyncio
async def test_no_profile_at_all_is_a_blocker(session):
    product = await _ready_product(session)
    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is False
    assert "listing_profile" in _fields(report, BLOCKER)
    assert report.profile_id is None


@pytest.mark.asyncio
async def test_a_profile_nobody_chose_is_not_applied(session):
    """A complete profile existing is not the same as it having been picked for this
    product: there is no platform default, so the product is blocked until someone does."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session)
    (await session.execute(select(ProductPlatformSettings))).scalar_one().listing_profile_id = None
    await session.commit()

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is False
    assert "listing_profile" in _fields(report, BLOCKER)


@pytest.mark.asyncio
async def test_each_missing_required_etsy_field_is_named_individually(session):
    """One message per missing field, each naming where to set it — a single "profile
    incomplete" would make the user hunt for which of seven fields it meant."""
    await _complete_etsy_profile(
        session,
        etsy_taxonomy_id=None,
        etsy_who_made=None,
        etsy_shipping_profile_id=None,
        etsy_readiness_state_id=None,
    )
    product = await _ready_product(session)

    report = await evaluate(session, product.id, ETSY)
    blockers = _fields(report, BLOCKER)
    assert "etsy_taxonomy_id" in blockers
    assert "etsy_who_made" in blockers
    assert "etsy_shipping_profile_id" in blockers
    assert "etsy_readiness_state_id" in blockers
    assert "etsy_when_made" not in blockers
    assert all(i.fix_hint for i in report.issues if i.severity == BLOCKER and i.field.startswith("etsy_"))


@pytest.mark.asyncio
async def test_missing_description_blocks_because_etsy_requires_it(session):
    """The state every active product in the live catalogue is currently in."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session, description=None)

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is False
    assert "description" in _fields(report, BLOCKER)


@pytest.mark.asyncio
async def test_listing_description_satisfies_the_requirement(session):
    """The inventory description isn't the only source — listing copy counts."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session, description=None, listing_description="SEO body copy.")

    report = await evaluate(session, product.id, ETSY)
    assert "description" not in _fields(report, BLOCKER)


@pytest.mark.asyncio
async def test_missing_image_is_only_a_warning(session):
    """createDraftListing lists image_ids as optional — a draft can exist without one, and
    only publishing needs it. Blocking here would stop work that Etsy itself allows."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session)

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is True
    assert "images" in _fields(report, WARNING)


@pytest.mark.asyncio
async def test_no_price_anywhere_blocks(session):
    await _complete_etsy_profile(session)
    product = await _ready_product(session, sale_price=None)

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is False
    assert "price" in _fields(report, BLOCKER)


@pytest.mark.asyncio
async def test_some_unpriced_variants_warn_rather_than_block(session):
    """A unit with no price is left out of the listing rather than failing the whole push —
    the same partial tolerance listing_sync already applies per unit."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session, sale_price=None)
    session.add_all(
        [
            ProductVariant(
                product_id=product.id,
                variant_name="Blue",
                sku_suffix="BLUE",
                is_active=True,
                sale_price=Decimal("12.50"),
            ),
            ProductVariant(product_id=product.id, variant_name="Teal", sku_suffix="TEAL", is_active=True),
        ]
    )
    await session.commit()

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is True
    assert "price" in _fields(report, WARNING)
    assert report.unit_count == 2 and report.priced_unit_count == 1


@pytest.mark.asyncio
async def test_variants_inherit_the_product_price(session):
    await _complete_etsy_profile(session)
    product = await _ready_product(session, sale_price=Decimal("9.99"))
    session.add(
        ProductVariant(product_id=product.id, variant_name="Blue", sku_suffix="BLUE", is_active=True)
    )
    await session.commit()

    report = await evaluate(session, product.id, ETSY)
    assert report.priced_unit_count == 1
    assert "price" not in _fields(report)


@pytest.mark.asyncio
async def test_a_hard_limit_breach_blocks_the_draft(session):
    """Conformance and completeness both have to pass — a valid profile doesn't help if
    the SKU is longer than the marketplace accepts."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session, sku="S" * 40)

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is False
    assert "sku_max_length" in _fields(report, BLOCKER)


@pytest.mark.asyncio
async def test_warnings_from_the_compatibility_panel_are_not_repeated_here(session):
    """A long title is a warning the compatibility panel already reports. Repeating it
    would show one problem twice, in two places, worded differently."""
    await _complete_etsy_profile(session)
    product = await _ready_product(session, name="T" * 200)

    report = await evaluate(session, product.id, ETSY)
    assert "title_max_length" not in _fields(report)


@pytest.mark.asyncio
async def test_resolved_title_is_reported_with_its_source(session):
    await _complete_etsy_profile(session)
    product = await _ready_product(session, listing_title="Brick Pencil Pot | Desk Tidy")

    report = await evaluate(session, product.id, ETSY)
    assert report.title == "Brick Pencil Pot | Desk Tidy"
    assert report.title_source == "shared"


@pytest.mark.asyncio
async def test_the_products_chosen_profile_is_the_one_evaluated(session):
    complete = await _complete_etsy_profile(session, name="Complete")
    incomplete = ListingProfile(platform=ETSY, name="Incomplete")
    session.add(incomplete)
    await session.commit()

    product = await _ready_product(session, profile=incomplete)

    report = await evaluate(session, product.id, ETSY)
    assert report.profile_name == "Incomplete"
    assert report.profile_id != complete.id
    assert report.can_create is False


@pytest.mark.asyncio
async def test_ebay_requires_a_different_set_of_fields(session):
    """The two marketplaces need different metadata, so an Etsy-complete profile says
    nothing about eBay readiness."""
    session.add(
        ListingProfile(
            platform=EBAY,
            name="Default",
            ebay_category_id="12345",
            ebay_condition="NEW",
        )
    )
    await session.commit()
    product = await _ready_product(session)

    report = await evaluate(session, product.id, EBAY)
    blockers = _fields(report, BLOCKER)
    assert "ebay_fulfillment_policy_id" in blockers
    assert "ebay_merchant_location_key" in blockers
    assert "ebay_category_id" not in blockers
    # Etsy's fields are irrelevant here and must not leak into eBay's report.
    assert not any(f.startswith("etsy_") for f in blockers)


@pytest.mark.asyncio
async def test_is_supply_is_a_warning_not_a_blocker(session):
    """Etsy lists is_supply as optional while who_made and when_made both say they require
    it. Flagged rather than enforced, because that reading is inferred from the docs and
    has not been confirmed against a live call."""
    await _complete_etsy_profile(session, etsy_is_supply=None)
    product = await _ready_product(session)

    report = await evaluate(session, product.id, ETSY)
    assert report.can_create is True
    assert "etsy_is_supply" in _fields(report, WARNING)


# --- Shipping: the product's ShippingProfile is the primary source, the listing profile
# the fallback. Three situations, three messages, so the fix hint is the right one.


async def _shipping_profile(session, **overrides):
    from app.models.shipping_profile import ShippingProfile

    fields = dict(name="Small parcel", price=Decimal("3.50"))
    fields.update(overrides)
    profile = ShippingProfile(**fields)
    session.add(profile)
    await session.commit()
    return profile


@pytest.mark.asyncio
async def test_a_linked_shipping_profile_wins_over_the_listing_profile(session):
    await _complete_etsy_profile(session, etsy_shipping_profile_id=99)
    shipping = await _shipping_profile(session, etsy_shipping_profile_id=555)
    product = await _ready_product(session, shipping_profile_id=shipping.id)

    report = await evaluate(session, product.id, ETSY)
    assert report.shipping.value == 555
    assert report.shipping_source == "shipping_profile"
    assert "Small parcel" in report.shipping_source_label
    assert "etsy_shipping_profile_id" not in _fields(report)


@pytest.mark.asyncio
async def test_the_listing_profile_is_the_fallback_when_the_shipping_profile_is_unlinked(session):
    await _complete_etsy_profile(session, etsy_shipping_profile_id=99)
    shipping = await _shipping_profile(session)
    product = await _ready_product(session, shipping_profile_id=shipping.id)

    report = await evaluate(session, product.id, ETSY)
    assert report.shipping.value == 99
    assert report.shipping_source == "listing_profile"
    assert "etsy_shipping_profile_id" not in _fields(report)


@pytest.mark.asyncio
async def test_an_unlinked_shipping_profile_with_no_fallback_says_to_link_it(session):
    await _complete_etsy_profile(session, etsy_shipping_profile_id=None)
    shipping = await _shipping_profile(session)
    product = await _ready_product(session, shipping_profile_id=shipping.id)

    report = await evaluate(session, product.id, ETSY)
    issue = next(i for i in report.issues if i.field == "etsy_shipping_profile_id")
    assert issue.severity == BLOCKER
    assert "not linked to Etsy" in issue.message
    assert "Link 'Small parcel'" in issue.fix_hint
    assert "fallback" in issue.fix_hint  # the listing profile exists, so it is offered too
    assert report.shipping_source is None


@pytest.mark.asyncio
async def test_no_shipping_profile_at_all_says_to_assign_one(session):
    await _complete_etsy_profile(session, etsy_shipping_profile_id=None)
    product = await _ready_product(session)

    report = await evaluate(session, product.id, ETSY)
    issue = next(i for i in report.issues if i.field == "etsy_shipping_profile_id")
    assert "no shipping profile" in issue.message
    assert "Assign the product a shipping profile" in issue.fix_hint


@pytest.mark.asyncio
async def test_variants_sharing_one_linked_profile_count_as_the_products(session):
    """A draft is one listing covering every variant, so one shared variant-level profile
    is as good as a product-level one — but variants that differ can't be sent as one id."""
    await _complete_etsy_profile(session, etsy_shipping_profile_id=None)
    shared = await _shipping_profile(session, etsy_shipping_profile_id=555)
    other = await _shipping_profile(session, name="Large parcel", etsy_shipping_profile_id=556)
    product = await _ready_product(session)
    red = ProductVariant(product_id=product.id, variant_name="Red", shipping_profile_id=shared.id)
    blue = ProductVariant(product_id=product.id, variant_name="Blue", shipping_profile_id=shared.id)
    session.add_all([red, blue])
    await session.commit()
    report = await evaluate(session, product.id, ETSY)
    assert report.shipping.value == 555

    blue.shipping_profile_id = other.id
    await session.commit()
    report = await evaluate(session, product.id, ETSY)
    assert report.shipping.value is None


@pytest.mark.asyncio
async def test_ebay_uses_the_fulfillment_policy_link(session):
    profile = ListingProfile(
        platform=EBAY,
        name="Default",
        ebay_category_id="123",
        ebay_condition="NEW",
        ebay_fulfillment_policy_id="fallback-policy",
        ebay_payment_policy_id="p",
        ebay_return_policy_id="r",
        ebay_merchant_location_key="loc",
    )
    session.add(profile)
    shipping = await _shipping_profile(session, ebay_fulfillment_policy_id="policy-42")
    product = await _ready_product(session, shipping_profile_id=shipping.id)

    report = await evaluate(session, product.id, EBAY)
    assert report.shipping.value == "policy-42"
    assert report.shipping_source == "shipping_profile"
