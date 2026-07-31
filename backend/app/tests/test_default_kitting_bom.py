"""Default kitting BOM (Settings > General): a user-configured list of materials that
gets snapshotted onto every new product's kitting BOM at creation time. Replaces the
auto-created "4x6 Direct Thermal Label" material from a prior release, which silently
created an untracked, always-zero-stock ghost material whenever no existing material
happened to have that exact name.
"""

from decimal import Decimal

from sqlalchemy import func, select

from app.models.kitting import DefaultKittingMaterial, ProductKittingMaterial
from app.models.material import Material, MaterialCategory, MaterialUnit
from app.models.product import Product
from app.routers.products import create_product
from app.schemas.product import ProductCreate
from app.services.csv_io import import_products_csv
from app.services.kitting import apply_default_kitting_bom, get_default_kitting_bom, replace_default_kitting_bom


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def _real_material(session, name: str = "Real Label", qty: Decimal = Decimal("500")) -> Material:
    material = Material(name=name, category=MaterialCategory.packaging, unit=MaterialUnit.each, current_qty=qty)
    session.add(material)
    await session.commit()
    return material


async def test_apply_default_kitting_bom_is_a_noop_when_nothing_configured(session):
    product = Product(name="Widget", sku="SKU-1")
    session.add(product)
    await session.flush()

    await apply_default_kitting_bom(session, product.id)
    await session.commit()

    assert await _count(session, ProductKittingMaterial) == 0
    assert await _count(session, Material) == 0  # never creates a material out of thin air


async def test_apply_default_kitting_bom_snapshots_configured_lines_onto_new_product(session):
    material = await _real_material(session)
    await replace_default_kitting_bom(session, [(material.id, Decimal("1"))])

    product = Product(name="Widget", sku="SKU-1")
    session.add(product)
    await session.flush()
    await apply_default_kitting_bom(session, product.id)
    await session.commit()

    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert line.material_id == material.id
    assert Decimal(line.qty_required) == Decimal("1")
    assert await _count(session, Material) == 1  # only the real material — no ghost created


async def test_editing_default_afterward_does_not_change_existing_products(session):
    """The core promise: this is a snapshot, not a live reference."""
    material_a = await _real_material(session, "Box")
    await replace_default_kitting_bom(session, [(material_a.id, Decimal("1"))])

    product = Product(name="Widget", sku="SKU-1")
    session.add(product)
    await session.flush()
    await apply_default_kitting_bom(session, product.id)
    await session.commit()

    material_b = await _real_material(session, "Bigger Box")
    await replace_default_kitting_bom(session, [(material_b.id, Decimal("2"))])

    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert line.material_id == material_a.id
    assert Decimal(line.qty_required) == Decimal("1")


async def test_create_product_endpoint_applies_configured_default(session):
    material = await _real_material(session)
    await replace_default_kitting_bom(session, [(material.id, Decimal("1"))])

    product = await create_product(ProductCreate(name="Widget D"), session=session)

    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert line.material_id == material.id


async def test_create_product_endpoint_attaches_nothing_when_no_default_configured(session):
    """The regression this whole feature exists to prevent: no configured default means
    no kitting BOM line, and definitely no auto-created ghost material."""
    product = await create_product(ProductCreate(name="Widget E"), session=session)

    assert await _count(session, ProductKittingMaterial) == 0
    assert await _count(session, Material) == 0


async def test_csv_import_applies_configured_default_to_new_products(session):
    material = await _real_material(session)
    await replace_default_kitting_bom(session, [(material.id, Decimal("1"))])

    csv_content = b"name,sku,description\nWidget C,SKU-C,A widget\n"
    result = await import_products_csv(session, csv_content)

    assert result["created"] == 1
    product = (await session.execute(select(Product).where(Product.sku == "SKU-C"))).scalar_one()
    line = (
        await session.execute(select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product.id))
    ).scalar_one()
    assert line.material_id == material.id


async def test_replace_default_kitting_bom_replaces_not_appends(session):
    material_a = await _real_material(session, "Box")
    material_b = await _real_material(session, "Label")

    await replace_default_kitting_bom(session, [(material_a.id, Decimal("1"))])
    await replace_default_kitting_bom(session, [(material_b.id, Decimal("2"))])

    lines = await get_default_kitting_bom(session)
    assert len(lines) == 1
    assert lines[0].material_id == material_b.id
    assert Decimal(lines[0].qty_required) == Decimal("2")


async def test_default_kitting_bom_settings_endpoint_round_trips(session):
    from app.routers.fee_config import get_default_kitting_bom_route, update_default_kitting_bom
    from app.schemas.kitting import KittingBomLine

    material = await _real_material(session)

    updated = await update_default_kitting_bom(
        [KittingBomLine(material_id=material.id, qty_required=Decimal("3"))], session=session
    )
    assert len(updated) == 1
    assert updated[0].material_id == material.id

    fetched = await get_default_kitting_bom_route(session=session)
    assert len(fetched) == 1
    assert Decimal(fetched[0].qty_required) == Decimal("3")


async def test_default_kitting_bom_unique_per_material(session):
    """DB-level guard: replace_default_kitting_bom is the only writer, but the unique
    constraint on material_id is what actually prevents a duplicate line for the same
    material if that ever changes."""
    from sqlalchemy.exc import IntegrityError

    material = await _real_material(session)
    session.add(DefaultKittingMaterial(material_id=material.id, qty_required=Decimal("1")))
    session.add(DefaultKittingMaterial(material_id=material.id, qty_required=Decimal("2")))
    try:
        await session.commit()
        assert False, "expected a unique-constraint violation"
    except IntegrityError:
        await session.rollback()
