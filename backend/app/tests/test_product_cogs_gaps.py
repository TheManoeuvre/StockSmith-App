"""The products list's COGS columns and its "Incomplete COGS only" filter.

A product with no shipping profile is what leaves an order with no postage cost to freeze,
and nothing surfaced that until a shipped order's profit was already wrong. These cover the
rule that decides what counts as a gap — particularly the case that must NOT count, since a
flag that fires on a legitimate setup is one users learn to ignore.
"""

from decimal import Decimal

from app.models.material import Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant
from app.routers.products import list_products
from app.services import material_categories


async def _profile(session, name="Small Parcel 48") -> ShippingProfile:
    profile = ShippingProfile(
        name=name,
        price=Decimal("3.60"),
        cost_etsy=Decimal("3.65"),
        cost_ebay=Decimal("3.20"),
        cost_manual=Decimal("3.65"),
    )
    session.add(profile)
    await session.flush()
    return profile


async def _costed_product(session, name: str, *, profile: ShippingProfile | None) -> Product:
    """A product with a real BOM, so the materials half of the rule is satisfied and each
    test isolates the half it's actually about."""
    category = await material_categories.find_or_create(session, "filament")
    material = Material(
        name=f"Filament for {name}",
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.g,
        avg_unit_cost=Decimal("0.05"),
    )
    session.add(material)
    await session.flush()
    product = Product(name=name, sku=name, shipping_profile_id=profile.id if profile else None)
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal("10")))
    await session.flush()
    return product


async def _variant(session, product: Product, name: str, *, profile: ShippingProfile | None) -> ProductVariant:
    variant = ProductVariant(
        product_id=product.id,
        variant_name=name,
        attribute1_value=name,
        shipping_profile_id=profile.id if profile else None,
    )
    session.add(variant)
    await session.flush()
    return variant


async def _read(session, product_id: int):
    page = await list_products(limit=200, offset=0, session=session)
    return next(item for item in page.items if item.id == product_id)


async def test_product_with_no_shipping_profile_is_flagged(session):
    product = await _costed_product(session, "Unprofiled", profile=None)
    await session.commit()

    read = await _read(session, product.id)
    assert read.cogs_incomplete is True
    assert read.effective_shipping_profile_id is None
    assert read.effective_shipping_cost is None


async def test_variants_carrying_their_own_profile_are_not_a_gap(session):
    """resolve_variant_shipping_profile checks the variant before falling back to the
    product, and the order-level default goes through it — so a product with no profile of
    its own whose every active variant sets one ships perfectly well. Flagging it would be a
    false alarm."""
    profile = await _profile(session)
    product = await _costed_product(session, "CoveredByVariants", profile=None)
    await _variant(session, product, "Red", profile=profile)
    await _variant(session, product, "Blue", profile=profile)
    await session.commit()

    assert (await _read(session, product.id)).cogs_incomplete is False


async def test_one_uncovered_variant_is_enough_to_flag(session):
    profile = await _profile(session)
    product = await _costed_product(session, "PartlyCovered", profile=None)
    await _variant(session, product, "Red", profile=profile)
    await _variant(session, product, "Blue", profile=None)
    await session.commit()

    assert (await _read(session, product.id)).cogs_incomplete is True


async def test_an_inactive_uncovered_variant_does_not_flag(session):
    """A disabled variant isn't sellable, so it can't produce an order with no postage cost."""
    profile = await _profile(session)
    product = await _costed_product(session, "DisabledGap", profile=None)
    await _variant(session, product, "Red", profile=profile)
    disabled = await _variant(session, product, "Retired", profile=None)
    disabled.is_active = False
    await session.commit()

    assert (await _read(session, product.id)).cogs_incomplete is False


async def test_product_with_no_bom_is_flagged(session):
    profile = await _profile(session)
    product = Product(name="NoBom", sku="NoBom", shipping_profile_id=profile.id)
    session.add(product)
    await session.commit()

    read = await _read(session, product.id)
    assert read.cogs_incomplete is True
    assert read.cost_per_unit is None


async def test_a_product_with_no_packaging_is_not_flagged(session):
    """None means "no packaging", not "packaging is free" — the codebase is explicit about
    that distinction, so a missing kitting BOM is a legitimate configuration."""
    profile = await _profile(session)
    product = await _costed_product(session, "Unpackaged", profile=profile)
    await session.commit()

    read = await _read(session, product.id)
    assert read.kitting_cost_per_unit is None
    assert read.cogs_incomplete is False


async def test_effective_shipping_cost_uses_the_margin_fee_source(session):
    profile = await _profile(session)
    product = await _costed_product(session, "Priced", profile=profile)
    await session.commit()

    read = await _read(session, product.id)
    assert read.effective_shipping_profile_name == "Small Parcel 48"
    # The default margin fee source is manual, so cost_manual is the figure — the same basis
    # the product page's margin estimate uses.
    assert Decimal(read.effective_shipping_cost) == Decimal("3.65")


async def test_the_filter_narrows_the_page_and_the_count_survives_it(session):
    profile = await _profile(session)
    await _costed_product(session, "Complete", profile=profile)
    gap = await _costed_product(session, "Gap", profile=None)
    await session.commit()

    everything = await list_products(limit=200, offset=0, session=session)
    assert everything.total == 2
    # The count is reported whether or not the filter is on, so the toggle can say how many
    # products it would reveal before being switched on.
    assert everything.incomplete_total == 1

    filtered = await list_products(limit=200, offset=0, cogs_incomplete=True, session=session)
    assert filtered.total == 1
    assert filtered.incomplete_total == 1
    assert [item.id for item in filtered.items] == [gap.id]


async def test_search_matches_name_or_sku_and_the_counts_follow(session):
    profile = await _profile(session)
    keyring = await _costed_product(session, "Oak Leaf Keyring", profile=profile)
    coaster = await _costed_product(session, "Slate Coaster", profile=None)  # a COGS gap
    await session.commit()

    by_name = await list_products(limit=200, offset=0, q="keyring", session=session)
    assert [i.id for i in by_name.items] == [keyring.id]
    assert by_name.total == 1
    # incomplete_total is scoped by the search too — the coaster is filtered out.
    assert by_name.incomplete_total == 0

    by_sku = await list_products(limit=200, offset=0, q=coaster.sku, session=session)
    assert [i.id for i in by_sku.items] == [coaster.id]
    assert by_sku.total == 1
    assert by_sku.incomplete_total == 1

    assert (await list_products(limit=200, offset=0, q="nothing matches this", session=session)).total == 0
