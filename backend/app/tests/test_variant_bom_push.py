"""Editing a variant's BOM must reach the marketplace.

A BOM change moves max_buildable, which moves theoretical_max_sellable, which is the
quantity listing_push sends. routers/variants.py did not import listing_push at all, so a
corrected BOM sat locally until some unrelated stock movement happened to trigger a push —
silent, and in the direction that oversells.
"""

from decimal import Decimal

import pytest_asyncio

from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant
from app.schemas.variant import VariantBomLine
import app.routers.variants as variants_router


@pytest_asyncio.fixture
async def variant(session):
    session.add(
        Material(id=1, name="Filament", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g)
    )
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.flush()
    session.add(ProductMaterial(product_id=1, material_id=1, qty_required=Decimal("10")))
    v = ProductVariant(id=5, product_id=1, variant_name="Large")
    session.add(v)
    await session.commit()
    return v


async def test_replacing_bom_overrides_pushes_to_the_marketplace(session, variant, pushes):
    await variants_router.replace_bom_overrides(
        variant_id=5,
        payload=[VariantBomLine(material_id=1, qty_required=Decimal("14"), replaces_material_id=None)],
        session=session,
    )

    assert ("product", 1, 5) in pushes


async def test_replacing_kitting_overrides_pushes_to_the_marketplace(session, variant, pushes):
    await variants_router.replace_kitting_bom_overrides(variant_id=5, payload=[], session=session)

    assert ("product", 1, 5) in pushes
