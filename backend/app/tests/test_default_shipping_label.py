"""Item 3: every new product should get 1x '4x6 Direct Thermal Label' seeded onto its
kitting BOM by default, since every item needs a shipping label — both for the
single-product creation path and for bulk CSV import."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models.kitting import ProductKittingMaterial
from app.models.material import Material, MaterialCategory, MaterialUnit
from app.models.product import Product
from app.routers.products import create_product
from app.schemas.product import ProductCreate
from app.services.csv_io import import_products_csv
from app.services.kitting import DEFAULT_SHIPPING_LABEL_MATERIAL_NAME, attach_default_shipping_label


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_attach_default_shipping_label_creates_material_and_bom_line(session):
    product = Product(name="Widget", sku="SKU-1")
    session.add(product)
    await session.flush()

    await attach_default_shipping_label(session, product.id)
    await session.commit()

    material = (
        await session.execute(select(Material).where(Material.name == DEFAULT_SHIPPING_LABEL_MATERIAL_NAME))
    ).scalar_one()
    assert material.category == MaterialCategory.packaging
    assert material.unit == MaterialUnit.each

    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert line.material_id == material.id
    assert Decimal(line.qty_required) == Decimal(1)


async def test_attach_default_shipping_label_reuses_existing_material(session):
    """Get-or-create by name, not a fresh row every time — otherwise a second product
    would fork off its own '4x6 Direct Thermal Label' with a separate cost/stock history."""
    product_a = Product(name="Widget A", sku="SKU-A")
    product_b = Product(name="Widget B", sku="SKU-B")
    session.add_all([product_a, product_b])
    await session.flush()

    await attach_default_shipping_label(session, product_a.id)
    await attach_default_shipping_label(session, product_b.id)
    await session.commit()

    assert await _count(session, Material) == 1
    assert await _count(session, ProductKittingMaterial) == 2


async def test_create_product_endpoint_attaches_default_label(session):
    product = await create_product(ProductCreate(name="Widget D"), session=session)

    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    material = await session.get(Material, line.material_id)
    assert material.name == DEFAULT_SHIPPING_LABEL_MATERIAL_NAME
    assert Decimal(line.qty_required) == Decimal(1)


async def test_csv_import_attaches_default_label_to_new_products(session):
    csv_content = b"name,sku,description\nWidget C,SKU-C,A widget\n"

    result = await import_products_csv(session, csv_content)

    assert result["created"] == 1
    product = (await session.execute(select(Product).where(Product.sku == "SKU-C"))).scalar_one()
    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert Decimal(line.qty_required) == Decimal(1)
