"""Dashboard inventory value (buildability.compute_dashboard_summary).

The tile used to be materials only, serialised as the raw float-derived Decimal. It now
adds finished goods on hand at resolved build-BOM cost and returns pence, with the split
alongside the total.
"""

from decimal import Decimal

from app.models.material import Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.services import material_categories
from app.services.buildability import compute_dashboard_summary


async def _material(session, name: str, unit_cost: str, qty: str = "0", is_active: bool = True) -> Material:
    category = await material_categories.find_or_create(session, "filament")
    material = Material(
        name=name,
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.g,
        avg_unit_cost=Decimal(unit_cost),
        current_qty=Decimal(qty),
        is_active=is_active,
    )
    session.add(material)
    await session.flush()
    return material


async def test_inventory_value_sums_materials_and_finished_goods_to_the_penny(session):
    # Materials: 10 @ 0.333 = 3.33 (rounded), 4 @ 1.25 = 5.00; an inactive one is ignored.
    filament = await _material(session, "Filament", "0.333", qty="10")
    insert = await _material(session, "Insert", "1.25", qty="4")
    await _material(session, "Retired", "9.99", qty="100", is_active=False)

    # A product with no variants: 3 on hand at base BOM cost (2 × 0.333 + 1 × 1.25 = 1.916).
    plain = Product(name="Plain", sku="PLAIN", current_stock=3)
    session.add(plain)
    await session.flush()
    session.add_all(
        [
            ProductMaterial(product_id=plain.id, material_id=filament.id, qty_required=Decimal(2)),
            ProductMaterial(product_id=plain.id, material_id=insert.id, qty_required=Decimal(1)),
        ]
    )

    # A product with variants: the variant stock counts (at the variant's resolved cost), the
    # product-level stock does not — that's the convention forecasting uses too.
    varied = Product(name="Varied", sku="VAR", current_stock=50)
    session.add(varied)
    await session.flush()
    session.add(ProductMaterial(product_id=varied.id, material_id=filament.id, qty_required=Decimal(4)))
    heavy = ProductVariant(product_id=varied.id, variant_name="Heavy", attribute1_value="Heavy", current_stock=2)
    light = ProductVariant(product_id=varied.id, variant_name="Light", attribute1_value="Light", current_stock=5)
    retired = ProductVariant(
        product_id=varied.id, variant_name="Old", attribute1_value="Old", current_stock=7, is_active=False
    )
    session.add_all([heavy, light, retired])
    await session.flush()
    # Heavy overrides the filament qty to 10 (cost 3.33); Light inherits 4 (cost 1.332).
    session.add(ProductVariantMaterial(variant_id=heavy.id, material_id=filament.id, qty_required=Decimal(10)))

    # A product with a BOM cost but no stock, and one with stock but no BOM: neither counts.
    empty = Product(name="Empty", sku="EMPTY", current_stock=0)
    costless = Product(name="Costless", sku="NOBOM", current_stock=9)
    session.add_all([empty, costless])
    await session.flush()
    session.add(ProductMaterial(product_id=empty.id, material_id=insert.id, qty_required=Decimal(1)))
    await session.commit()

    summary = await compute_dashboard_summary(session)

    assert summary.material_value == Decimal("8.33")
    # 3 × 1.916 + 2 × 3.33 + 5 × 1.332 = 5.748 + 6.66 + 6.66 = 19.068
    assert summary.finished_goods_value == Decimal("19.07")
    assert summary.total_inventory_value == Decimal("27.40")
    assert str(summary.total_inventory_value) == "27.40"  # pence, not a float-expanded Decimal
