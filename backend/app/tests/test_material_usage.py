"""The "used in N products" count behind the material detail panel's footer
(_USED_IN_PRODUCT_COUNT_SQL in routers/materials.py)."""

from decimal import Decimal

from app.models.kitting import ProductKittingMaterial
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.routers.materials import _USED_IN_PRODUCT_COUNT_SQL


async def _count(session, material_id: int) -> int:
    return (
        await session.execute(_USED_IN_PRODUCT_COUNT_SQL, {"material_id": material_id})
    ).scalar_one()


async def test_counts_distinct_products_across_bom_and_kitting(session):
    m = Material(name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g)
    other = Material(name="Resin", category=LegacyMaterialCategory.resin, unit=MaterialUnit.ml)
    session.add_all([m, other])
    await session.flush()

    a = Product(name="Keyring", sku="K-1")
    b = Product(name="Coaster", sku="C-1")
    session.add_all([a, b])
    await session.flush()

    # Product A uses the material in its build BOM *and* its kitting BOM — still one product.
    session.add(ProductMaterial(product_id=a.id, material_id=m.id, qty_required=Decimal(5)))
    session.add(ProductKittingMaterial(product_id=a.id, material_id=m.id, qty_required=Decimal(1)))
    # Product B uses it only for kitting.
    session.add(ProductKittingMaterial(product_id=b.id, material_id=m.id, qty_required=Decimal(1)))
    await session.commit()

    assert await _count(session, m.id) == 2
    assert await _count(session, other.id) == 0
