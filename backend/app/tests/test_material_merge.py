"""Merging one material into another (services/material_merge).

reference_data.merge is a plain repoint; this one has to cope with every unique key that
spans a material FK, and with stock and cost being derived from history rather than
stored. So the tests are one per collision rule, plus the replay.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.kitting import DefaultKittingMaterial, ProductKittingMaterial
from app.models.material import LegacyMaterialCategory, Material, MaterialAdjustment, MaterialAdjustmentMode, MaterialUnit
from app.models.material_substitute import MaterialSubstitute
from app.models.product import Product, ProductMaterial
from app.models.purchase import MaterialPurchase
from app.models.stock_take import StockTake, StockTakeLine, StockTakeStatus
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.routers.materials import merge_material, preview_material_merge, update_material
from app.schemas.material import MaterialMergeRequest, MaterialUpdate
from app.services import material_merge
from app.services.costing import recompute_materials
from app.services.reference_data import InUseError, ReferenceDataError
from app.tests.conftest import received_purchase

A, B, C = 1, 2, 3  # source, target, bystander


async def _materials(session, *, unit_b=MaterialUnit.g):
    session.add_all([
        Material(id=A, name="Brick 2x4 Red", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g,
                 barcode="111", allocated_qty=Decimal(0)),
        Material(id=B, name="2x4 Brick Red", category=LegacyMaterialCategory.filament, unit=unit_b),
        Material(id=C, name="Glue", category=LegacyMaterialCategory.other, unit=MaterialUnit.each),
    ])
    await session.flush()


async def _product(session, product_id=1):
    p = Product(id=product_id, name=f"Widget {product_id}", sku=f"W-{product_id}")
    session.add(p)
    await session.flush()
    return p


class TestStockAndCost:
    async def test_histories_replay_into_a_weighted_average(self, session, pushes):
        await _materials(session)
        now = datetime.now(timezone.utc)
        await received_purchase(session, A, 100, 100, now - timedelta(days=2))  # £1.00/g
        await received_purchase(session, B, 100, 300, now - timedelta(days=1))  # £3.00/g
        session.add(MaterialAdjustment(material_id=A, mode=MaterialAdjustmentMode.adjust, qty_delta=Decimal(-20), reason="used"))
        await session.commit()

        target = await material_merge.merge(session, A, B)

        assert target.id == B
        assert Decimal(target.current_qty) == Decimal(180)
        assert Decimal(target.avg_unit_cost) == Decimal(2)
        assert await session.get(Material, A) is None
        lines = list((await session.execute(select(MaterialPurchase))).scalars())
        assert {l.material_id for l in lines} == {B}
        assert ("material", B) in pushes

    async def test_allocated_is_summed_and_fields_backfilled(self, session, pushes):
        await _materials(session)
        a = await session.get(Material, A)
        b = await session.get(Material, B)
        await received_purchase(session, A, 10, 10)
        await received_purchase(session, B, 10, 10)
        await recompute_materials(session, {A, B})  # receipts only count once replayed
        a.allocated_qty, b.allocated_qty = Decimal(3), Decimal(4)
        await session.commit()

        target = await material_merge.merge(session, A, B)

        assert Decimal(target.allocated_qty) == Decimal(7)
        assert target.barcode == "111"  # B had none

    async def test_unit_mismatch_refused(self, session, pushes):
        await _materials(session, unit_b=MaterialUnit.each)
        await session.commit()
        with pytest.raises(ReferenceDataError, match="different units"):
            await material_merge.plan_merge(session, A, B)

    async def test_self_and_inactive_target(self, session, pushes):
        await _materials(session)
        with pytest.raises(ReferenceDataError, match="itself"):
            await material_merge.plan_merge(session, A, A)
        (await session.get(Material, B)).is_active = False
        await session.commit()
        with pytest.raises(ReferenceDataError, match="deactivated"):
            await material_merge.plan_merge(session, A, B)


class TestCollisions:
    async def test_bom_lines_on_the_same_product_are_summed(self, session, pushes):
        await _materials(session)
        await _product(session, 1)
        await _product(session, 2)
        session.add_all([
            ProductMaterial(product_id=1, material_id=A, qty_required=Decimal(2)),
            ProductMaterial(product_id=1, material_id=B, qty_required=Decimal(3)),
            ProductMaterial(product_id=2, material_id=A, qty_required=Decimal(5)),
        ])
        await session.commit()

        plan = await material_merge.plan_merge(session, A, B)
        bom = next(e for e in plan.effects if e.label == "product BOM lines")
        assert (bom.repointed, bom.summed) == (1, 1)

        await material_merge.merge(session, A, B)

        rows = {r.product_id: r for r in (await session.execute(select(ProductMaterial))).scalars()}
        assert Decimal(rows[1].qty_required) == Decimal(5) and rows[1].material_id == B
        assert Decimal(rows[2].qty_required) == Decimal(5) and rows[2].material_id == B

    async def test_kitting_and_default_kitting(self, session, pushes):
        await _materials(session)
        await _product(session, 1)
        session.add_all([
            ProductKittingMaterial(product_id=1, material_id=A, qty_required=Decimal(1)),
            ProductKittingMaterial(product_id=1, material_id=B, qty_required=Decimal(1)),
            DefaultKittingMaterial(material_id=A, qty_required=Decimal("0.5")),
            DefaultKittingMaterial(material_id=B, qty_required=Decimal("0.5")),
        ])
        await session.commit()

        await material_merge.merge(session, A, B)

        kit = (await session.execute(select(ProductKittingMaterial))).scalar_one()
        assert kit.material_id == B and Decimal(kit.qty_required) == Decimal(2)
        default = (await session.execute(select(DefaultKittingMaterial))).scalar_one()
        assert default.material_id == B and Decimal(default.qty_required) == Decimal(1)

    async def test_variant_substitution_of_a_for_b_collapses(self, session, pushes):
        """A variant that swapped B for A now swaps B for B — meaningless, so the row goes."""
        await _materials(session)
        await _product(session, 1)
        session.add(ProductVariant(id=10, product_id=1, variant_name="V"))
        session.add(ProductVariant(id=11, product_id=1, variant_name="W"))
        await session.flush()
        session.add_all([
            ProductVariantMaterial(variant_id=10, material_id=A, qty_required=Decimal(4), replaces_material_id=B),
            # Unrelated substitution keeps working, just pointing at B.
            ProductVariantMaterial(variant_id=11, material_id=A, qty_required=Decimal(4), replaces_material_id=C),
        ])
        await session.commit()

        await material_merge.merge(session, A, B)

        rows = list((await session.execute(select(ProductVariantMaterial))).scalars())
        assert [(r.variant_id, r.material_id, r.replaces_material_id) for r in rows] == [(11, B, C)]

    async def test_substitute_rules_dedupe_and_drop_self_pairs(self, session, pushes):
        await _materials(session)
        session.add_all([
            MaterialSubstitute(material_id=A, substitute_material_id=B),  # A⇄B becomes B⇄B
            MaterialSubstitute(material_id=A, substitute_material_id=C),  # collides with B→C
            MaterialSubstitute(material_id=B, substitute_material_id=C),
            MaterialSubstitute(material_id=C, substitute_material_id=A),  # becomes C→B
        ])
        await session.commit()

        await material_merge.merge(session, A, B)

        rows = sorted(
            (r.material_id, r.substitute_material_id)
            for r in (await session.execute(select(MaterialSubstitute))).scalars()
        )
        assert rows == [(B, C), (C, B)]

    async def test_open_stock_take_blocks(self, session, pushes):
        await _materials(session)
        take = StockTake(status=StockTakeStatus.open, includes_materials=True)
        session.add(take)
        await session.flush()
        session.add(StockTakeLine(stock_take_id=take.id, material_id=B, expected_qty=Decimal(0)))
        await session.commit()

        plan = await material_merge.plan_merge(session, A, B)
        assert plan.blockers
        with pytest.raises(InUseError, match="stock take"):
            await material_merge.merge(session, A, B)
        assert await session.get(Material, A) is not None


class TestRouter:
    async def test_preview_and_merge(self, session, pushes):
        await _materials(session)
        await _product(session, 1)
        session.add(ProductMaterial(product_id=1, material_id=A, qty_required=Decimal(2)))
        await session.commit()

        plan = await preview_material_merge(A, MaterialMergeRequest(target_id=B), session)
        assert plan.source_name == "Brick 2x4 Red" and plan.target_name == "2x4 Brick Red"
        assert [(e.label, e.repointed, e.summed) for e in plan.effects] == [("product BOM lines", 1, 0)]

        read = await merge_material(A, MaterialMergeRequest(target_id=B), session)
        assert read.id == B

    async def test_errors_map_to_400_and_409(self, session, pushes):
        await _materials(session, unit_b=MaterialUnit.each)
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await preview_material_merge(A, MaterialMergeRequest(target_id=B), session)
        assert exc.value.status_code == 400

    async def test_rename_clash_is_409(self, session, pushes):
        await _materials(session)
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await update_material(A, MaterialUpdate(name="2x4 Brick Red"), session)
        assert exc.value.status_code == 409 and "already called" in exc.value.detail
