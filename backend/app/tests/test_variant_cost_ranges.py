"""Catalogue-wide variant cost ranges (buildability.get_cost_per_unit_range_by_product and
its packaging twin in kitting).

The point of these is that the products list previously showed a base-BOM-only figure for a
product whose variants override their BOM — a number that can match no actual variant. The
new SQL resolves overrides and substitutions exactly as the existing per-product resolvers
do, so the tests below assert agreement with those rather than against hand-computed
constants: the per-product path is the established source of truth, and a range that
disagrees with it is wrong however plausible its arithmetic looks.
"""

from decimal import Decimal

from app.models.kitting import ProductKittingMaterial, ProductVariantKittingMaterial
from app.models.material import Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.services import material_categories
from app.services.buildability import (
    compute_variants_buildability_bulk,
    get_cost_per_unit_by_product,
    get_cost_per_unit_range_by_product,
)
from app.services.kitting import (
    get_kitting_cost_per_unit_range_by_product,
    get_resolved_kitting_bom,
    kitting_cost_per_unit_from_bom,
)


async def _material(session, name: str, unit_cost: str) -> Material:
    category = await material_categories.find_or_create(session, "filament")
    material = Material(
        name=name,
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.g,
        avg_unit_cost=Decimal(unit_cost),
    )
    session.add(material)
    await session.flush()
    return material


async def _variant(session, product: Product, name: str, *, is_active: bool = True) -> ProductVariant:
    variant = ProductVariant(product_id=product.id, variant_name=name, attribute1_value=name, is_active=is_active)
    session.add(variant)
    await session.flush()
    return variant


async def _product_with_overriding_variants(session) -> tuple[Product, dict[str, ProductVariant]]:
    """One product exercising every arm of the BOM resolution at once: a plain variant that
    inherits the base BOM, one with a qty override, and one that substitutes a base material
    for a different one. Plus an inactive variant deliberately made the most expensive of
    the four, so a range that wrongly includes it is impossible to miss.
    """
    base_a = await _material(session, "Base A", "1.00")
    base_b = await _material(session, "Base B", "2.00")
    swapped = await _material(session, "Swapped In", "0.50")
    pricey = await _material(session, "Pricey", "99.00")

    product = Product(name="Ranged", sku="SKU-RANGE")
    session.add(product)
    await session.flush()
    # Base BOM: 2 x A (£2.00) + 3 x B (£6.00) = £8.00 per unit.
    session.add(ProductMaterial(product_id=product.id, material_id=base_a.id, qty_required=Decimal("2")))
    session.add(ProductMaterial(product_id=product.id, material_id=base_b.id, qty_required=Decimal("3")))

    plain = await _variant(session, product, "Plain")
    override = await _variant(session, product, "Override")
    substitute = await _variant(session, product, "Substitute")
    disabled = await _variant(session, product, "Disabled", is_active=False)

    # Qty override: 5 x A instead of 2 (£5.00 + £6.00 = £11.00).
    session.add(
        ProductVariantMaterial(variant_id=override.id, material_id=base_a.id, qty_required=Decimal("5"))
    )
    # Substitution: B swapped for the cheaper material, 1 unit (£2.00 + £0.50 = £2.50).
    session.add(
        ProductVariantMaterial(
            variant_id=substitute.id,
            material_id=swapped.id,
            qty_required=Decimal("1"),
            replaces_material_id=base_b.id,
        )
    )
    # The inactive variant is by far the dearest, and must not appear in the range.
    session.add(
        ProductVariantMaterial(variant_id=disabled.id, material_id=pricey.id, qty_required=Decimal("1"))
    )
    await session.commit()
    return product, {"plain": plain, "override": override, "substitute": substitute, "disabled": disabled}


async def test_range_agrees_with_the_per_product_resolver(session):
    product, variants = await _product_with_overriding_variants(session)
    active_ids = [variants["plain"].id, variants["override"].id, variants["substitute"].id]

    per_variant = await compute_variants_buildability_bulk(session, product.id, active_ids)
    expected_costs = [per_variant[vid][2] for vid in active_ids]

    ranges = await get_cost_per_unit_range_by_product(session)
    cost_min, cost_max = ranges[product.id]

    assert cost_min == min(expected_costs)
    assert cost_max == max(expected_costs)
    # Guards the test itself: if the fixture stopped exercising overrides the range would
    # collapse and the assertions above would pass trivially.
    assert cost_min != cost_max


async def test_range_excludes_inactive_variants(session):
    product, variants = await _product_with_overriding_variants(session)

    _, cost_max = (await get_cost_per_unit_range_by_product(session))[product.id]

    disabled_cost = (await compute_variants_buildability_bulk(session, product.id, [variants["disabled"].id]))[
        variants["disabled"].id
    ][2]
    assert disabled_cost > cost_max


async def test_range_spans_what_the_base_only_figure_hides(session):
    """The reason this exists: the base-BOM figure sits inside the real spread, so a product
    list showing it alone reports a cost no variant actually has."""
    product, _ = await _product_with_overriding_variants(session)

    base_only = (await get_cost_per_unit_by_product(session))[product.id]
    cost_min, cost_max = (await get_cost_per_unit_range_by_product(session))[product.id]

    assert cost_min < base_only < cost_max


async def test_product_without_variants_has_no_range(session):
    """Absent rather than a zero-width range, matching how get_cost_per_unit_by_product omits
    a product with no BOM instead of reporting it as free."""
    material = await _material(session, "Only", "1.50")
    product = Product(name="Plain", sku="SKU-PLAIN")
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal("2")))
    await session.commit()

    assert product.id not in await get_cost_per_unit_range_by_product(session)
    assert (await get_cost_per_unit_by_product(session))[product.id] == Decimal("3.00")


async def test_kitting_range_agrees_with_the_per_variant_resolver(session):
    """Same shape for packaging — the kitting SQL is the twin of the materials one, so it
    gets the same override-and-substitution treatment rather than being taken on trust."""
    box = await _material(session, "Box", "0.50")
    big_box = await _material(session, "Big Box", "1.25")
    label = await _material(session, "Label", "0.01")

    product = Product(name="Boxed", sku="SKU-BOX")
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=Decimal("1")))
    session.add(ProductKittingMaterial(product_id=product.id, material_id=label.id, qty_required=Decimal("1")))

    small = await _variant(session, product, "Small")
    large = await _variant(session, product, "Large")
    session.add(
        ProductVariantKittingMaterial(
            variant_id=large.id,
            material_id=big_box.id,
            qty_required=Decimal("1"),
            replaces_material_id=box.id,
        )
    )
    await session.commit()

    expected = [
        kitting_cost_per_unit_from_bom(await _costed_kitting_bom(session, product.id, v.id))
        for v in (small, large)
    ]
    cost_min, cost_max = (await get_kitting_cost_per_unit_range_by_product(session))[product.id]

    assert cost_min == min(expected)
    assert cost_max == max(expected)
    assert cost_min == Decimal("0.51")  # box + label
    assert cost_max == Decimal("1.26")  # big box + label


async def _costed_kitting_bom(session, product_id: int, variant_id: int):
    """get_resolved_kitting_bom returns lines with unit_cost unpopulated (only the capacity
    paths fill it in), so attach the costs here before kitting_cost_per_unit_from_bom."""
    bom = await get_resolved_kitting_bom(session, product_id, variant_id)
    for line in bom:
        material = await session.get(Material, line.material_id)
        line.unit_cost = Decimal(material.avg_unit_cost)
    return bom
