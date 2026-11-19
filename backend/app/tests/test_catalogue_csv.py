"""Variants and BOMs CSV: round-trips, whole-recipe replacement, and refusing bad rows without
half-writing a recipe."""

import csv
import io
from decimal import Decimal

from sqlalchemy import select

from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.services import catalogue_csv


def _csv(rows: list[dict], fields: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})
    return buf.getvalue().encode()


def _boms(*rows: dict) -> bytes:
    return _csv(list(rows), catalogue_csv.BOMS_CSV_FIELDS)


def _variants(*rows: dict) -> bytes:
    return _csv(list(rows), catalogue_csv.VARIANTS_CSV_FIELDS)


async def _material(session, name: str, category: str = "filament", unit: str = "g") -> Material:
    cat = (await session.scalars(select(MaterialCategory).where(MaterialCategory.name == category))).one()
    m = Material(name=name, category=category, category_id=cat.id, unit=unit)
    session.add(m)
    await session.commit()
    return m


async def _setup(session):
    black = await _material(session, "Black PLA")
    white = await _material(session, "White PLA")
    box = await _material(session, "Small box", "packaging", "each")
    product = Product(name="Plant pot", sku="POT", current_stock=0, allocated_qty=0)
    session.add(product)
    await session.commit()
    return product, black, white, box


async def _base(session, product) -> dict[int, Decimal]:
    rows = (await session.scalars(select(ProductMaterial).where(ProductMaterial.product_id == product.id))).all()
    return {r.material_id: Decimal(r.qty_required) for r in rows}


class TestVariants:
    async def test_creates_then_updates_by_product_sku_and_name(self, session, pushes):
        product, *_ = await _setup(session)
        result = await catalogue_csv.import_variants_csv(
            session,
            _variants(
                {"product_sku": "POT", "variant_name": "Small", "attribute1": "Small", "sale_price": "8.50"},
                {"product_sku": "POT", "variant_name": "Large", "attribute1": "Large", "sale_price": "12"},
            ),
        )
        assert result == {"created": 2, "updated": 0, "failed": []}

        # A blank cell leaves the field alone; a filled one changes it.
        result = await catalogue_csv.import_variants_csv(
            session, _variants({"product_sku": "POT", "variant_name": "Large", "sale_price": "13", "is_active": "no"})
        )
        assert result == {"created": 0, "updated": 1, "failed": []}
        large = (await session.scalars(select(ProductVariant).where(ProductVariant.variant_name == "Large"))).one()
        assert (Decimal(large.sale_price), large.is_active, large.attribute1_value) == (Decimal("13"), False, "Large")

    async def test_reports_bad_rows_and_keeps_the_good_ones(self, session, pushes):
        await _setup(session)
        result = await catalogue_csv.import_variants_csv(
            session,
            _variants(
                {"product_sku": "NOPE", "variant_name": "Small"},
                {"product_sku": "POT", "variant_name": ""},
                {"product_sku": "POT", "variant_name": "Small", "sale_price": "cheap"},
                {"product_sku": "POT", "variant_name": "Medium"},
            ),
        )
        assert result["created"] == 1
        assert [f["row"] for f in result["failed"]] == [2, 3, 4]
        assert "import products first" in result["failed"][0]["error"]
        assert "must be a number" in result["failed"][2]["error"]

    async def test_round_trips(self, session, pushes):
        await _setup(session)
        original = _variants({"product_sku": "POT", "variant_name": "Small", "attribute1": "Small", "sale_price": "8.50"})
        await catalogue_csv.import_variants_csv(session, original)
        exported = await catalogue_csv.export_variants_csv(session)
        assert "POT,Plant pot,Small,,Small,,,8.50,true" in exported
        assert (await catalogue_csv.import_variants_csv(session, exported.encode()))["updated"] == 1


class TestBoms:
    async def test_replaces_a_products_whole_recipe(self, session, pushes):
        product, black, white, box = await _setup(session)
        session.add(ProductMaterial(product_id=product.id, material_id=box.id, qty_required=1))
        await session.commit()

        result = await catalogue_csv.import_boms_csv(
            session,
            _boms(
                {"product_sku": "POT", "material": "Black PLA", "qty_required": "45"},
                {"product_sku": "POT", "material": "white pla", "qty_required": "5"},
            ),
        )
        assert result == {"created": 0, "updated": 1, "failed": []}
        # Exactly the lines listed: the box is gone, materials matched regardless of case.
        assert await _base(session, product) == {black.id: Decimal(45), white.id: Decimal(5)}
        assert ("product", product.id, None) in pushes

    async def test_variant_overrides_and_swaps(self, session, pushes):
        product, black, white, _ = await _setup(session)
        await catalogue_csv.import_variants_csv(session, _variants({"product_sku": "POT", "variant_name": "White"}))
        result = await catalogue_csv.import_boms_csv(
            session,
            _boms(
                # Variant rows listed first: the product's own recipe is still applied before them.
                {"product_sku": "POT", "variant_name": "White", "material": "White PLA", "qty_required": "45", "replaces_material": "Black PLA"},
                {"product_sku": "POT", "material": "Black PLA", "qty_required": "45"},
            ),
        )
        assert result == {"created": 2, "updated": 0, "failed": []}
        override = (await session.scalars(select(ProductVariantMaterial))).one()
        assert (override.material_id, override.replaces_material_id, Decimal(override.qty_required)) == (
            white.id,
            black.id,
            Decimal(45),
        )

    async def test_a_bad_row_leaves_that_recipe_untouched(self, session, pushes):
        product, black, _, box = await _setup(session)
        session.add(ProductMaterial(product_id=product.id, material_id=black.id, qty_required=40))
        await session.commit()

        result = await catalogue_csv.import_boms_csv(
            session,
            _boms(
                {"product_sku": "POT", "material": "Black PLA", "qty_required": "45"},
                {"product_sku": "POT", "material": "Unobtainium", "qty_required": "1"},
                {"product_sku": "POT", "material": "Small box", "qty_required": "1.5"},
            ),
        )
        assert result["created"] == result["updated"] == 0
        assert [f["row"] for f in result["failed"]] == [3]
        assert "no material is called 'Unobtainium'" in result["failed"][0]["error"]
        assert await _base(session, product) == {black.id: Decimal(40)}

    async def test_unit_rules_still_apply(self, session, pushes):
        """'each' takes whole numbers — the same rule the BOM editor enforces."""
        product, _, _, box = await _setup(session)
        result = await catalogue_csv.import_boms_csv(
            session, _boms({"product_sku": "POT", "material": "Small box", "qty_required": "1.5"})
        )
        assert len(result["failed"]) == 1
        assert "recipe wasn't changed" in result["failed"][0]["error"]
        assert await _base(session, product) == {}

    async def test_variant_rules_still_apply(self, session, pushes):
        """A swap must replace something in the product's recipe — the variant editor's rule."""
        product, black, white, _ = await _setup(session)
        await catalogue_csv.import_variants_csv(session, _variants({"product_sku": "POT", "variant_name": "White"}))
        result = await catalogue_csv.import_boms_csv(
            session,
            _boms(
                {"product_sku": "POT", "variant_name": "White", "material": "White PLA", "qty_required": "45", "replaces_material": "Black PLA"},
            ),
        )
        assert len(result["failed"]) == 1
        assert "not part of the base BOM" in result["failed"][0]["error"]

    async def test_round_trips(self, session, pushes):
        product, black, white, _ = await _setup(session)
        await catalogue_csv.import_variants_csv(session, _variants({"product_sku": "POT", "variant_name": "White"}))
        source = _boms(
            {"product_sku": "POT", "material": "Black PLA", "qty_required": "45"},
            {"product_sku": "POT", "variant_name": "White", "material": "White PLA", "qty_required": "45", "replaces_material": "Black PLA"},
        )
        await catalogue_csv.import_boms_csv(session, source)
        exported = await catalogue_csv.export_boms_csv(session)
        assert exported.splitlines() == [
            ",".join(catalogue_csv.BOMS_CSV_FIELDS),
            "POT,Plant pot,,Black PLA,45,",
            "POT,Plant pot,White,White PLA,45,Black PLA",
        ]
        assert (await catalogue_csv.import_boms_csv(session, exported.encode()))["updated"] == 2
