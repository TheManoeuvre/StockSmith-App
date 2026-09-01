"""Two small single-material-detail queries in routers/materials.py:
_USED_IN_PRODUCT_COUNT_SQL (the "used in N products" footer) and
_OPEN_STOCK_TAKE_LINE_SQL (the Counting tab's "On stock take" row)."""

from decimal import Decimal

from app.models.kitting import ProductKittingMaterial
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus
from app.routers.materials import _OPEN_STOCK_TAKE_LINE_SQL, _USED_IN_PRODUCT_COUNT_SQL


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


async def test_open_stock_take_line_is_found_only_on_the_open_take(session):
    m = Material(name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g)
    untouched = Material(name="Resin", category=LegacyMaterialCategory.resin, unit=MaterialUnit.ml)
    session.add_all([m, untouched])
    await session.flush()

    closed = StockTake(status=StockTakeStatus.closed, includes_materials=True)
    closed.lines = [
        StockTakeLine(material_id=m.id, expected_qty=Decimal(10), status=StockTakeLineStatus.applied)
    ]
    open_take = StockTake(status=StockTakeStatus.open, includes_materials=True)
    open_take.lines = [
        StockTakeLine(material_id=m.id, expected_qty=Decimal(12), status=StockTakeLineStatus.conflict)
    ]
    session.add_all([closed, open_take])
    await session.commit()

    row = (await session.execute(_OPEN_STOCK_TAKE_LINE_SQL, {"material_id": m.id})).first()
    assert row is not None
    assert row.stock_take_id == open_take.id
    assert row.status == "conflict"

    # Not on the open take at all — the closed take's line for the same material doesn't count.
    assert (
        await session.execute(_OPEN_STOCK_TAKE_LINE_SQL, {"material_id": untouched.id})
    ).first() is None
