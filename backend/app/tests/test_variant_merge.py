"""Merging one variant into a sibling (services/variant_merge).

The merge is a composition of things the app already does — substitute an order line,
adjust stock, push a listing — so these tests check the composition: that each piece is
called with the right variant, in an order that leaves both ledgers reconciled, and that
the loser ends up disabled with its SKU remembered rather than deleted.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.asset import AssetType, ProductAsset
from app.models.listing import Listing, ListingPlatform
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.order import OrderLine
from app.models.order_substitution import OrderLineSubstitution
from app.models.platform_listing_push import ListingPushStatus
from app.models.product import Product, ProductMaterial
from app.models.product_stock_event import ProductStockEvent
from app.models.sku_alias import SkuAlias
from app.models.stock_adjustment import StockAdjustment
from app.models.stock_take import StockTake, StockTakeLine, StockTakeStatus
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.routers.orders import create_order
from app.routers.variants import merge_variant, preview_variant_merge
from app.schemas.order import OrderCreate, OrderLineInput
from app.schemas.variant import VariantMergePreviewRequest, VariantMergeRequest
from app.services import listing_push, variant_merge
from app.services.variant_merge import VariantMergeError

FILAMENT, OAK = 1, 2


async def _setup(session, *, loser_stock=4, survivor_stock=6, loser_override=True):
    """Pencil pot with two "4 Stud" variants: the survivor on the base BOM, the loser (by
    default) overriding Filament with Oak."""
    session.add_all([
        Material(id=FILAMENT, name="Filament", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g),
        Material(id=OAK, name="Oak", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g),
    ])
    product = Product(id=1, name="Brick Pencil Pot", sku="BPP", variant_attribute1_name="Size")
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=1, material_id=FILAMENT, qty_required=Decimal("10")))
    survivor = ProductVariant(
        id=10, product_id=1, variant_name="4 Stud", sku_suffix="01", attribute1_value="4 Stud",
        current_stock=survivor_stock,
    )
    loser = ProductVariant(
        id=11, product_id=1, variant_name="4 Stud Standard", sku_suffix="02", attribute1_value="4 Stud Standard",
        current_stock=loser_stock,
    )
    session.add_all([survivor, loser])
    await session.flush()
    if loser_override:
        session.add(
            ProductVariantMaterial(variant_id=11, material_id=OAK, qty_required=Decimal("12"), replaces_material_id=FILAMENT)
        )
    await session.commit()
    return product, loser, survivor


async def _adjustments(session, variant_id):
    return list(
        (await session.execute(select(StockAdjustment).where(StockAdjustment.variant_id == variant_id))).scalars()
    )


class TestPlan:
    async def test_reports_stock_bom_and_no_blockers(self, session, pushes):
        await _setup(session)

        plan = await variant_merge.plan_merge(session, 11, 10)

        assert plan.stock_to_move == 4
        assert plan.bom_differs is True
        assert plan.kitting_differs is False
        assert [l.material_name for l in plan.loser_bom] == ["Oak"]
        assert plan.loser_bom[0].replaces_material_name == "Filament"
        assert [l.material_name for l in plan.survivor_bom] == ["Filament"]
        assert plan.loser.full_sku == "BPP-02"
        assert plan.blockers == []
        assert plan.live_listings == []

    async def test_identical_boms_do_not_differ(self, session, pushes):
        await _setup(session, loser_override=False)
        plan = await variant_merge.plan_merge(session, 11, 10)
        assert plan.bom_differs is False

    async def test_lists_open_lines_only(self, session, pushes):
        await _setup(session, loser_stock=5)
        await create_order(
            OrderCreate(lines=[OrderLineInput(variant_id=11, ordered_qty=2, unit_price=Decimal("10"))]),
            session=session,
        )

        plan = await variant_merge.plan_merge(session, 11, 10)

        assert [l.qty for l in plan.open_lines] == [2]

    async def test_open_stock_take_is_a_blocker(self, session, pushes):
        await _setup(session)
        take = StockTake(status=StockTakeStatus.open, includes_products=True)
        session.add(take)
        await session.flush()
        session.add(StockTakeLine(stock_take_id=take.id, product_id=1, variant_id=10, expected_qty=6))
        await session.commit()

        plan = await variant_merge.plan_merge(session, 11, 10)

        assert any("stock take" in b for b in plan.blockers)
        with pytest.raises(VariantMergeError, match="stock take"):
            await variant_merge.apply_merge(session, 11, 10)


class TestRefusals:
    async def test_self(self, session, pushes):
        await _setup(session)
        with pytest.raises(VariantMergeError, match="itself"):
            await variant_merge.plan_merge(session, 11, 11)

    async def test_other_product(self, session, pushes):
        await _setup(session)
        other = Product(id=2, name="Other", sku="OTH")
        session.add(other)
        await session.flush()
        session.add(ProductVariant(id=20, product_id=2, variant_name="X"))
        await session.commit()
        with pytest.raises(VariantMergeError, match="same product"):
            await variant_merge.plan_merge(session, 11, 20)

    async def test_disabled_survivor(self, session, pushes):
        _, _, survivor = await _setup(session)
        survivor.is_active = False
        await session.commit()
        with pytest.raises(VariantMergeError, match="disabled"):
            await variant_merge.plan_merge(session, 11, 10)

    async def test_router_maps_to_400(self, session, pushes):
        await _setup(session)
        with pytest.raises(HTTPException) as exc:
            await preview_variant_merge(11, VariantMergePreviewRequest(target_id=11), session)
        assert exc.value.status_code == 400


class TestApply:
    async def test_moves_stock_with_two_adjustments_and_closes_the_loser_ledger(self, session, pushes):
        await _setup(session)

        outcome = await variant_merge.apply_merge(session, 11, 10)

        loser = await session.get(ProductVariant, 11)
        survivor = await session.get(ProductVariant, 10)
        assert outcome.stock_moved == 4
        assert loser.current_stock == 0
        assert loser.is_active is False
        assert survivor.current_stock == 10
        assert [a.qty_delta for a in await _adjustments(session, 11)] == [-4]
        assert [a.qty_delta for a in await _adjustments(session, 10)] == [4]
        events = list(
            (await session.execute(select(ProductStockEvent).where(ProductStockEvent.variant_id == 11))).scalars()
        )
        assert events[-1].running_balance == 0
        assert ("owner", survivor) in pushes

    async def test_zero_stock_writes_no_adjustment(self, session, pushes):
        await _setup(session, loser_stock=0)
        await variant_merge.apply_merge(session, 11, 10)
        assert await _adjustments(session, 11) == []
        assert await _adjustments(session, 10) == []

    async def test_keep_survivor_drops_loser_overrides(self, session, pushes):
        await _setup(session)
        await variant_merge.apply_merge(session, 11, 10, bom="keep_survivor")
        rows = list((await session.execute(select(ProductVariantMaterial))).scalars())
        assert rows == []

    async def test_take_loser_moves_overrides_onto_survivor(self, session, pushes):
        await _setup(session)
        await variant_merge.apply_merge(session, 11, 10, bom="take_loser")
        rows = list((await session.execute(select(ProductVariantMaterial))).scalars())
        assert [(r.variant_id, r.material_id, r.replaces_material_id) for r in rows] == [(10, OAK, FILAMENT)]

    async def test_open_order_line_follows_to_survivor(self, session, pushes):
        await _setup(session, loser_stock=5, survivor_stock=0)
        order = await create_order(
            OrderCreate(lines=[OrderLineInput(variant_id=11, ordered_qty=2, unit_price=Decimal("10"))]),
            session=session,
        )
        assert order.lines[0].allocated_qty == 2

        outcome = await variant_merge.apply_merge(session, 11, 10)

        assert outcome.open_lines_moved == 1
        lines = {l.variant_id: l for l in (await session.execute(select(OrderLine))).scalars()}
        assert lines[11].ordered_qty == 0
        assert lines[10].ordered_qty == 2
        # The survivor now holds all five units and the two reserved for the order.
        survivor = await session.get(ProductVariant, 10)
        assert survivor.current_stock == 5
        assert survivor.allocated_qty == 2
        assert lines[10].allocated_qty == 2
        loser = await session.get(ProductVariant, 11)
        assert loser.allocated_qty == 0
        subs = list((await session.execute(select(OrderLineSubstitution))).scalars())
        assert len(subs) == 1 and "Merged" in subs[0].reason

    async def test_shipped_line_stays_on_loser(self, session, pushes):
        await _setup(session, loser_stock=5)
        order = await create_order(
            OrderCreate(lines=[OrderLineInput(variant_id=11, ordered_qty=2, unit_price=Decimal("10"))]),
            session=session,
        )
        line = await session.get(OrderLine, order.lines[0].id)
        from app.services import allocation

        await allocation.ship_line(session, line, 2)
        await session.commit()

        outcome = await variant_merge.apply_merge(session, 11, 10)

        assert outcome.open_lines_moved == 0
        await session.refresh(line)
        assert line.variant_id == 11 and line.shipped_qty == 2
        # 5 - 2 shipped = 3 moved.
        assert outcome.stock_moved == 3

    async def test_loser_sku_becomes_alias_for_survivor(self, session, pushes):
        await _setup(session)
        await variant_merge.apply_merge(session, 11, 10)
        aliases = list((await session.execute(select(SkuAlias).where(SkuAlias.external_sku == "BPP-02"))).scalars())
        assert {a.platform for a in aliases} == set(ListingPlatform)
        assert all(a.variant_id == 10 and a.product_id == 1 for a in aliases)

    async def test_existing_aliases_and_assets_repoint(self, session, pushes):
        await _setup(session)
        session.add(SkuAlias(platform=ListingPlatform.etsy, external_sku="OLD-SKU", product_id=1, variant_id=11))
        session.add(
            ProductAsset(
                product_id=1, variant_id=11, asset_type=AssetType.main_image, file_path="x.png", original_filename="x.png"
            )
        )
        await session.commit()

        await variant_merge.apply_merge(session, 11, 10)

        alias = (await session.execute(select(SkuAlias).where(SkuAlias.external_sku == "OLD-SKU"))).scalar_one()
        assert alias.variant_id == 10
        asset = (await session.execute(select(ProductAsset))).scalar_one()
        assert asset.variant_id == 10


class TestLiveListings:
    async def _with_live_listing(self, session):
        await _setup(session)
        session.add(
            Listing(
                product_id=1, variant_id=11, platform=ListingPlatform.etsy, external_listing_id="555",
                published_sku="BPP-02",
            )
        )
        await session.commit()

    async def test_ask_refuses_with_409(self, session, pushes):
        await self._with_live_listing(session)
        with pytest.raises(HTTPException) as exc:
            await variant_merge.apply_merge(session, 11, 10)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "live_listing_conflicts"
        assert exc.value.detail["conflicts"][0]["platform"] == "etsy"
        # Nothing happened.
        assert (await session.get(ProductVariant, 11)).is_active is True

    async def test_proceed_pushes_zero_then_disables(self, session, pushes, monkeypatch):
        await self._with_live_listing(session)
        pushed: list[tuple[int, int]] = []

        async def fake_push_one(session_, listing, qty):
            pushed.append((listing.variant_id, qty))
            return ListingPushStatus.success, None

        monkeypatch.setattr(listing_push, "_push_one", fake_push_one)

        outcome = await variant_merge.apply_merge(session, 11, 10, on_live_listing="proceed")

        assert pushed == [(11, 0)]
        assert outcome.warnings == []
        assert (await session.get(ProductVariant, 11)).is_active is False

    async def test_push_failure_is_a_warning_not_an_abort(self, session, pushes, monkeypatch):
        await self._with_live_listing(session)

        async def fake_push_one(session_, listing, qty):
            return ListingPushStatus.error, "boom"

        monkeypatch.setattr(listing_push, "_push_one", fake_push_one)

        outcome = await variant_merge.apply_merge(session, 11, 10, on_live_listing="proceed")

        assert len(outcome.warnings) == 1 and "etsy" in outcome.warnings[0]
        assert (await session.get(ProductVariant, 11)).is_active is False

    async def test_router_result_shape(self, session, pushes, monkeypatch):
        await self._with_live_listing(session)

        async def fake_push_one(session_, listing, qty):
            return ListingPushStatus.success, None

        monkeypatch.setattr(listing_push, "_push_one", fake_push_one)

        result = await merge_variant(
            11, VariantMergeRequest(target_id=10, bom="take_loser", on_live_listing="proceed"), session
        )

        assert result.survivor.id == 10
        assert result.stock_moved == 4
        assert result.survivor.current_stock == 10
