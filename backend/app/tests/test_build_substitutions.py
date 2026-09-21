"""Per-build material substitution (services/builds.py `substitutions`).

The fallback-pooled capacity figures already assume a short material can be built with
one of its curated fallbacks; this is the flow that actually draws the stock from the
fallback. Covers: the stock movement landing on the substitute, the audit row, failed-unit
consumption following the swap, and the two rejections (not a curated fallback, not on
the BOM)."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.build import BuildFailedConsumption
from app.models.material import LegacyMaterialCategory, Material, MaterialAdjustment, MaterialUnit
from app.models.material_substitute import MaterialSubstitute, MaterialSubstituteUsage
from app.models.product import Product, ProductMaterial
from app.services import material_categories
from app.services.builds import create_build
from app.services.costing import recompute_material


async def _material(session, name: str, qty: Decimal) -> Material:
    category = await material_categories.find_or_create(session, "filament")
    m = Material(name=name, category=LegacyMaterialCategory.filament, category_id=category.id, unit=MaterialUnit.g)
    session.add(m)
    await session.flush()
    if qty:
        session.add(MaterialAdjustment(material_id=m.id, qty_delta=qty, reason="seed"))
        await recompute_material(session, m.id)
    return m


async def _setup(session) -> tuple[Product, Material, Material]:
    """A product needing 28 g of PETG White per unit, which is out of stock, with PLA Ivory
    curated as its fallback and plenty on the shelf."""
    petg = await _material(session, "PETG White", Decimal(0))
    pla = await _material(session, "PLA Ivory", Decimal(1000))
    session.add(MaterialSubstitute(material_id=petg.id, substitute_material_id=pla.id, rank=1))
    product = Product(name="Widget", sku="SKU-1", current_stock=0, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=petg.id, qty_required=Decimal(28)))
    await session.commit()
    return product, petg, pla


async def test_build_without_substitution_still_fails_on_the_short_material(session):
    product, _, _ = await _setup(session)
    with pytest.raises(HTTPException) as exc:
        await create_build(session, product.id, None, qty_built=1, notes=None)
    assert exc.value.status_code == 400


async def test_substituted_build_draws_from_the_fallback_and_logs_usage(session):
    product, petg, pla = await _setup(session)

    build = await create_build(
        session, product.id, None, qty_built=3, notes=None, substitutions={petg.id: pla.id}
    )

    await session.refresh(petg)
    await session.refresh(pla)
    assert Decimal(petg.current_qty) == Decimal(0)
    assert Decimal(pla.current_qty) == Decimal(1000) - Decimal(84)
    await session.refresh(product)
    assert product.current_stock == 3

    adjustment = (
        await session.execute(select(MaterialAdjustment).where(MaterialAdjustment.reason.like(f"Build #{build.id}%")))
    ).scalar_one()
    assert adjustment.material_id == pla.id
    assert "in place of PETG White" in adjustment.reason

    usage = (await session.execute(select(MaterialSubstituteUsage))).scalar_one()
    assert usage.material_id == petg.id
    assert usage.substitute_material_id == pla.id
    assert usage.build_id == build.id
    assert Decimal(usage.qty) == Decimal(84)


async def test_failed_units_consume_the_fallback_too(session):
    product, petg, pla = await _setup(session)

    build = await create_build(
        session, product.id, None, qty_built=1, notes=None, qty_failed=2, substitutions={petg.id: pla.id}
    )

    await session.refresh(pla)
    # 1 built + 2 failed (filament is consumed on a failed build by default) = 3 × 28 g.
    assert Decimal(pla.current_qty) == Decimal(1000) - Decimal(84)

    failed = (await session.execute(select(BuildFailedConsumption))).scalar_one()
    assert failed.material_id == pla.id
    assert failed.was_consumed is True
    assert Decimal(failed.qty_consumed) == Decimal(56)

    usage = (await session.execute(select(MaterialSubstituteUsage))).scalar_one()
    assert usage.build_id == build.id
    assert Decimal(usage.qty) == Decimal(84)


async def test_substitute_must_be_an_active_curated_fallback(session):
    product, petg, _ = await _setup(session)
    stranger = await _material(session, "ABS Grey", Decimal(1000))
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await create_build(session, product.id, None, qty_built=1, notes=None, substitutions={petg.id: stranger.id})
    assert exc.value.status_code == 400
    assert "not an active fallback" in exc.value.detail


async def test_substituted_material_must_be_on_the_bom(session):
    product, _, pla = await _setup(session)
    other = await _material(session, "TPU Black", Decimal(0))
    session.add(MaterialSubstitute(material_id=other.id, substitute_material_id=pla.id, rank=1))
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await create_build(session, product.id, None, qty_built=1, notes=None, substitutions={other.id: pla.id})
    assert exc.value.status_code == 400
    assert "not on this BOM" in exc.value.detail
