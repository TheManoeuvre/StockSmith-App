"""Storage locations and stock placements (docs/plan-locations.md).

Covers the range generator, the tree rules, placing and moving, and the rule that an item kept
in exactly one location follows its stock automatically.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.location import Location, PlacementEvent, PlacementEventKind, StockPlacement
from app.models.material import LegacyMaterialCategory, Material, MaterialAdjustmentMode, MaterialUnit
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import locations as locs
from app.services import placements
from app.services.costing import create_adjustment
from app.services.placement_hooks import ItemRef, following_paused


async def _material(session, mid=1, name="Soy wax", unit=MaterialUnit.g, qty=0):
    session.add(Material(id=mid, name=name, category=LegacyMaterialCategory.other, unit=unit))
    await session.commit()
    if qty:
        await create_adjustment(session, mid, MaterialAdjustmentMode.adjust, Decimal(qty), "opening")
    return ItemRef(material_id=mid)


async def _product(session, pid=1, stock=0, name="Candle"):
    session.add(Product(id=pid, name=name, sku=f"P-{pid}", current_stock=stock))
    await session.commit()
    return ItemRef(product_id=pid)


async def _qty(session, ref):
    rows = await placements.item_placements(session, ref)
    return {r.location_id: Decimal(r.qty) for r in rows}


async def _events(session, kind):
    return (await session.scalars(select(PlacementEvent).where(PlacementEvent.kind == kind))).all()


class TestRangePatterns:
    @pytest.mark.parametrize(
        "pattern, expected",
        [
            ("Box{01-05}", ["Box01", "Box02", "Box03", "Box04", "Box05"]),
            ("Box{1-3}", ["Box1", "Box2", "Box3"]),
            ("Bin-{A-C}", ["Bin-A", "Bin-B", "Bin-C"]),
            ("{a-c}", ["a", "b", "c"]),
            ("Col{Y-AB}", ["ColY", "ColZ", "ColAA", "ColAB"]),
            ("{A-B}{1-2}", ["A1", "A2", "B1", "B2"]),
            ("Shelf {9-11}", ["Shelf 9", "Shelf 10", "Shelf 11"]),
            ("{08-11}", ["08", "09", "10", "11"]),
        ],
    )
    def test_expands(self, pattern, expected):
        assert locs.expand_pattern(pattern) == expected

    def test_a_grid_is_row_major(self):
        names = locs.expand_pattern("{A-D}{1-6}")
        assert len(names) == 24 and names[0] == "A1" and names[-1] == "D6"

    @pytest.mark.parametrize(
        "pattern, message",
        [
            ("Box", "Put a range in braces"),
            ("{1-2}{1-2}{1-2}", "at most two ranges"),
            ("Box{5-1}", "comes before its start"),
            ("Box{x}", "can’t read"),
            ("{A-Z}{1-30}", "limit is 500"),
            ("{1-501}", "limit is 500"),
            ("Box{1-3", "braces"),
        ],
    )
    def test_refuses_with_a_message_fit_to_show(self, pattern, message):
        with pytest.raises(locs.RangeError, match=message):
            locs.expand_pattern(pattern)

    def test_exactly_500_is_allowed(self):
        assert len(locs.expand_pattern("{1-500}")) == 500


class TestTree:
    async def test_codes_come_from_the_id_and_never_change(self, session):
        a = await locs.create_location(session, "Workshop", None)
        assert a.code == f"LOC-{a.id:06d}"
        await locs.update_location(session, a.id, name="Studio")
        assert (await locs.get_location(session, a.id)).code == a.code

    async def test_sibling_names_are_unique_ignoring_case_but_cousins_are_not(self, session):
        a = await locs.create_location(session, "Shelf 1", None)
        b = await locs.create_location(session, "Loft", None)
        with pytest.raises(HTTPException) as exc:
            await locs.create_location(session, "shelf 1", None)
        assert exc.value.status_code == 409
        await locs.create_location(session, "Box", a.id)
        await locs.create_location(session, "Box", b.id)  # same name, different parent: fine
        with pytest.raises(HTTPException):
            await locs.create_location(session, "BOX", a.id)

    async def test_generate_range_skips_what_exists_and_never_overwrites(self, session):
        parent = await locs.create_location(session, "Stockroom", None)
        await locs.create_location(session, "Box02", parent.id)
        preview = await locs.preview_range(session, parent.id, "Box{01-05}")
        assert [n.exists for n in preview.names] == [False, True, False, False, False]
        assert preview.new_count == 4
        created = await locs.generate_range(session, parent.id, "Box{01-05}")
        assert [c.name for c in created] == ["Box01", "Box03", "Box04", "Box05"]

    async def test_a_range_that_makes_nothing_new_is_refused(self, session):
        await locs.generate_range(session, None, "Bin-{A-B}")
        with pytest.raises(HTTPException) as exc:
            await locs.generate_range(session, None, "Bin-{A-B}")
        assert exc.value.status_code == 400

    async def test_moving_a_location_into_itself_or_its_own_child_is_refused(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", a.id)
        for target in (a.id, b.id):
            with pytest.raises(HTTPException) as exc:
                await locs.move_location(session, a.id, target)
            assert exc.value.status_code == 400

    async def test_moving_a_location_carries_its_contents(self, session):
        shelf1 = await locs.create_location(session, "Shelf 1", None)
        shelf2 = await locs.create_location(session, "Shelf 2", None)
        box = await locs.create_location(session, "Box01", shelf1.id)
        item = await _material(session, qty=10)
        await placements.place(session, item, box.id, Decimal(10))
        await locs.move_location(session, box.id, shelf2.id)
        view = await placements.stored_in(session, item)
        assert view.placements[0].location_path == ["Shelf 2", "Box01"]

    async def test_a_move_that_collides_with_a_sibling_name_is_refused(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        await locs.create_location(session, "Box", a.id)
        box_b = await locs.create_location(session, "Box", b.id)
        with pytest.raises(HTTPException) as exc:
            await locs.move_location(session, box_b.id, a.id)
        assert exc.value.status_code == 409

    async def test_archiving_needs_the_location_empty(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=5)
        await placements.place(session, item, shelf.id, Decimal(5))
        with pytest.raises(HTTPException):
            await locs.set_archived(session, shelf.id, True)
        await placements.remove_placement(session, (await placements.item_placements(session, item))[0].id)
        archived = await locs.set_archived(session, shelf.id, True)
        assert archived.archived_at is not None
        with pytest.raises(HTTPException):  # can't place into an archived location
            await placements.place(session, item, shelf.id, Decimal(1))
        assert (await locs.set_archived(session, shelf.id, False)).archived_at is None

    async def test_archiving_needs_its_sub_locations_archived_first(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        await locs.create_location(session, "Box", shelf.id)
        with pytest.raises(HTTPException):
            await locs.set_archived(session, shelf.id, True)

    async def test_only_a_never_used_location_can_be_deleted(self, session):
        fresh = await locs.create_location(session, "Fresh", None)
        await locs.delete_location(session, fresh.id)
        used = await locs.create_location(session, "Used", None)
        item = await _material(session, qty=3)
        await placements.place(session, item, used.id, Decimal(3))
        await placements.remove_placement(session, (await placements.item_placements(session, item))[0].id)
        with pytest.raises(HTTPException) as exc:  # empty now, but it has history
            await locs.delete_location(session, used.id)
        assert exc.value.status_code == 409


class TestPlacing:
    async def test_the_first_placement_is_primary_and_unassigned_is_the_remainder(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=25)
        await placements.place(session, item, shelf.id, Decimal(10))
        view = await placements.stored_in(session, item)
        assert view.placements[0].is_primary
        assert (view.placed, view.unassigned, view.drift) == (Decimal(10), Decimal(15), Decimal(0))
        assert view.follows_stock

    async def test_you_cannot_place_more_than_is_unassigned(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, a.id, Decimal(8))
        with pytest.raises(HTTPException) as exc:
            await placements.place(session, item, b.id, Decimal(3))
        assert exc.value.status_code == 400 and "unassigned" in exc.value.detail

    async def test_whole_number_units_refuse_fractions(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, unit=MaterialUnit.each, qty=10)
        with pytest.raises(HTTPException):
            await placements.place(session, item, shelf.id, Decimal("2.5"))

    async def test_a_product_with_variants_must_be_placed_per_variant(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        product = await _product(session)
        session.add(ProductVariant(id=1, product_id=1, variant_name="Large", current_stock=4))
        await session.commit()
        with pytest.raises(HTTPException) as exc:
            await placements.place(session, product, shelf.id, Decimal(1))
        assert "variant" in exc.value.detail.lower()
        await placements.place(session, ItemRef(product_id=1, variant_id=1), shelf.id, Decimal(4))

    async def test_moving_part_of_an_item_keeps_its_total_and_merges_at_the_destination(self, session):
        loft = await locs.create_location(session, "Loft", None)
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=25)
        await placements.place(session, item, loft.id, Decimal(20))
        await placements.place(session, item, shelf.id, Decimal(5))
        await placements.move_item(session, item, loft.id, shelf.id, Decimal(4))
        assert await _qty(session, item) == {loft.id: Decimal(16), shelf.id: Decimal(9)}
        assert (await session.get(Material, 1)).current_qty == 25

    async def test_moving_everything_out_removes_the_row_and_hands_over_primary(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=6)
        await placements.place(session, item, a.id, Decimal(6))  # primary
        await placements.move_item(session, item, a.id, b.id, Decimal(6))
        rows = await placements.item_placements(session, item)
        assert [(r.location_id, r.is_primary) for r in rows] == [(b.id, True)]

    async def test_you_cannot_move_more_than_is_there(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=6)
        await placements.place(session, item, a.id, Decimal(6))
        with pytest.raises(HTTPException):
            await placements.move_item(session, item, a.id, b.id, Decimal(7))

    async def test_empty_into_merges_and_leaves_the_source_empty(self, session):
        box1 = await locs.create_location(session, "Box01", None)
        box2 = await locs.create_location(session, "Box02", None)
        wax = await _material(session, 1, "Wax", qty=13)
        wick = await _material(session, 2, "Wick", MaterialUnit.each, qty=40)
        await placements.place(session, wax, box1.id, Decimal(5))
        await placements.place(session, wax, box2.id, Decimal(8))
        await placements.place(session, wick, box1.id, Decimal(40))
        moved = await placements.empty_into(session, box1.id, box2.id)
        assert moved == 2
        assert await _qty(session, wax) == {box2.id: Decimal(13)}
        assert await _qty(session, wick) == {box2.id: Decimal(40)}
        assert (await session.scalars(select(StockPlacement).where(StockPlacement.location_id == box1.id))).all() == []

    async def test_make_primary_swaps_it(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, a.id, Decimal(5))
        pb = await placements.place(session, item, b.id, Decimal(5))
        await placements.make_primary(session, pb.id)
        view = await placements.stored_in(session, item)
        assert [p.location_id for p in view.placements if p.is_primary] == [b.id]


class TestAutomaticFollowing:
    """One location follows its stock both ways; several take decreases primary-first and leave
    increases Unassigned; none is left alone."""

    async def test_a_single_placement_is_reduced_by_a_decrease(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, shelf.id, Decimal(10))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-3), "used")
        assert await _qty(session, item) == {shelf.id: Decimal(7)}
        (event,) = await _events(session, PlacementEventKind.auto_deduct)
        assert (event.qty, event.from_location_id) == (3, shelf.id)

    async def test_a_single_placement_gains_from_an_increase(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, shelf.id, Decimal(4))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(6), "delivery")
        assert await _qty(session, item) == {shelf.id: Decimal(10)}
        assert len(await _events(session, PlacementEventKind.auto_add)) == 1

    async def test_a_decrease_is_clamped_at_what_is_placed(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, shelf.id, Decimal(4))  # 6 unassigned
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-8), "used")
        assert await _qty(session, item) == {shelf.id: Decimal(0)}
        view = await placements.stored_in(session, item)
        assert view.placed == 0 and view.unassigned == 2 and view.drift == 0

    async def test_several_placements_take_a_decrease_from_the_primary_first(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=12)
        await placements.place(session, item, a.id, Decimal(4))  # primary
        await placements.place(session, item, b.id, Decimal(8))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-3), "used")
        assert await _qty(session, item) == {a.id: Decimal(1), b.id: Decimal(8)}
        view = await placements.stored_in(session, item)
        assert (view.drift, view.follows_stock) == (Decimal(0), False)

    async def test_a_decrease_overflows_to_the_fullest_other_place(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        c = await locs.create_location(session, "C", None)
        item = await _material(session, qty=12)
        await placements.place(session, item, a.id, Decimal(2))  # primary
        await placements.place(session, item, b.id, Decimal(3))
        await placements.place(session, item, c.id, Decimal(7))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-5), "used")
        assert await _qty(session, item) == {a.id: Decimal(0), b.id: Decimal(3), c.id: Decimal(4)}
        events = await _events(session, PlacementEventKind.auto_deduct)
        assert sorted((e.from_location_id, e.qty) for e in events) == [(a.id, 2), (c.id, 3)]

    async def test_a_named_place_is_taken_from_first(self, session):
        from app.services.placement_hooks import taking_from

        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, a.id, Decimal(5))
        await placements.place(session, item, b.id, Decimal(5))
        with taking_from(b.id):
            await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-4), "used")
        assert await _qty(session, item) == {a.id: Decimal(5), b.id: Decimal(1)}

    async def test_the_adjustment_endpoints_accept_a_place_to_take_from(self, session):
        from app.routers.materials import create_material_adjustment
        from app.routers.stock_adjustments import record_stock_adjustment
        from app.schemas.material import MaterialAdjustmentCreate
        from app.schemas.stock_adjustment import StockAdjustmentCreate

        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        wax = await _material(session, qty=10)
        await placements.place(session, wax, a.id, Decimal(5))
        await placements.place(session, wax, b.id, Decimal(5))
        await create_material_adjustment(
            1, MaterialAdjustmentCreate(mode=MaterialAdjustmentMode.adjust, value=Decimal(-2), reason="used", from_location_id=b.id), session
        )
        assert await _qty(session, wax) == {a.id: Decimal(5), b.id: Decimal(3)}

        candle = await _product(session, stock=10)
        await placements.place(session, candle, a.id, Decimal(5))
        await placements.place(session, candle, b.id, Decimal(5))
        await record_stock_adjustment(
            StockAdjustmentCreate(product_id=1, value=-3, reason="gift", from_location_id=b.id), session
        )
        assert await _qty(session, candle) == {a.id: Decimal(5), b.id: Decimal(2)}

    async def test_several_placements_leave_an_increase_unassigned(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, a.id, Decimal(5))
        await placements.place(session, item, b.id, Decimal(5))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(6), "delivery")
        assert await _qty(session, item) == {a.id: Decimal(5), b.id: Decimal(5)}
        view = await placements.stored_in(session, item)
        assert view.unassigned == 6

    async def test_an_empty_primary_suggests_restocking_from_the_fullest_other(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=9)
        await placements.place(session, item, a.id, Decimal(2))  # primary
        await placements.place(session, item, b.id, Decimal(7))
        assert (await placements.stored_in(session, item)).replenish is None
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-2), "used")
        r = (await placements.stored_in(session, item)).replenish
        assert (r.to_location_id, r.from_location_id, r.qty) == (a.id, b.id, Decimal(7))

    async def test_an_unplaced_item_is_left_alone(self, session):
        item = await _material(session, qty=10)
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(5), "delivery")
        assert await _qty(session, item) == {}
        assert await _events(session, PlacementEventKind.auto_add) == []

    async def test_an_edit_that_changes_the_replayed_total_is_followed_too(self, session):
        """The reason this hooks the derived total: no per-event hook sees an old record change."""
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, shelf.id, Decimal(10))
        from app.models.material import MaterialAdjustment
        from app.services.costing import recompute_material

        adj = (await session.scalars(select(MaterialAdjustment))).first()
        adj.qty_delta = Decimal(7)  # the opening count is corrected from 10 to 7
        await recompute_material(session, 1)
        await session.commit()
        assert await _qty(session, item) == {shelf.id: Decimal(7)}

    async def test_a_product_follows_its_stock(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _product(session, stock=10)
        await placements.place(session, item, shelf.id, Decimal(10))
        product = await session.get(Product, 1)
        product.current_stock -= 4  # e.g. an order shipped
        await session.commit()
        assert await _qty(session, item) == {shelf.id: Decimal(6)}
        product.current_stock += 2  # e.g. a return
        await session.commit()
        assert await _qty(session, item) == {shelf.id: Decimal(8)}

    async def test_a_variant_follows_its_own_stock_only(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        await _product(session)
        session.add_all(
            [
                ProductVariant(id=1, product_id=1, variant_name="Small", current_stock=5),
                ProductVariant(id=2, product_id=1, variant_name="Large", current_stock=5),
            ]
        )
        await session.commit()
        small, large = ItemRef(product_id=1, variant_id=1), ItemRef(product_id=1, variant_id=2)
        await placements.place(session, small, shelf.id, Decimal(5))
        await placements.place(session, large, shelf.id, Decimal(5))
        (await session.get(ProductVariant, 2)).current_stock -= 2
        await session.commit()
        assert await _qty(session, small) == {shelf.id: Decimal(5)}
        assert await _qty(session, large) == {shelf.id: Decimal(3)}

    async def test_a_product_stock_change_with_no_net_difference_writes_nothing(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _product(session, stock=3)
        await placements.place(session, item, shelf.id, Decimal(3))
        product = await session.get(Product, 1)
        product.current_stock = 3
        await session.commit()
        assert await _events(session, PlacementEventKind.auto_deduct) == []


class TestDrift:
    async def test_reducing_a_placement_corrects_drift_and_keeps_the_total(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, a.id, Decimal(5))
        pb = await placements.place(session, item, b.id, Decimal(5))
        with following_paused():  # how drift arises now: a past record edited with no event to follow
            await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-2), "used")
        await placements.reduce_placement(session, pb.id, Decimal(2))
        view = await placements.stored_in(session, item)
        assert (view.placed, view.item.total, view.drift) == (Decimal(8), Decimal(8), Decimal(0))

    async def test_the_tree_summary_flags_the_drifting_branch_and_counts_unplaced(self, session):
        room = await locs.create_location(session, "Room", None)
        shelf = await locs.create_location(session, "Shelf", room.id)
        other = await locs.create_location(session, "Other", None)
        drifting = await _material(session, 1, "Wax", qty=10)
        await _material(session, 2, "Wicks", MaterialUnit.each, qty=5)  # never placed
        await placements.place(session, drifting, shelf.id, Decimal(5))
        await placements.place(session, drifting, other.id, Decimal(5))
        with following_paused():
            await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-3), "used")
        tree = await placements.tree_summary(session)
        by_name = {r.node.name: r for r in tree.rows}
        assert by_name["Shelf"].needs_attention and by_name["Room"].needs_attention
        assert by_name["Room"].rolled_up_items == 1 and by_name["Room"].own_items == 0
        assert tree.unplaced_items == 1
        assert tree.empty_locations == 0

    async def test_summaries_name_the_primary_location_and_count_the_rest(self, session):
        a = await locs.create_location(session, "Wax shelf", None)
        b = await locs.create_location(session, "Loft", None)
        item = await _material(session, qty=25)
        await placements.place(session, item, a.id, Decimal(5))
        await placements.place(session, item, b.id, Decimal(20))
        summary = (await placements.summaries(session))[(1, None, None)]
        assert summary.primary_path == ["Wax shelf"] and summary.extra_places == 1


class TestSchemaRules:
    async def test_one_primary_per_item_is_enforced_by_the_database(self, session):
        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", None)
        await _material(session, qty=4)
        from sqlalchemy.exc import IntegrityError

        session.add_all(
            [
                StockPlacement(location_id=a.id, material_id=1, qty=2, is_primary=True),
                StockPlacement(location_id=b.id, material_id=1, qty=2, is_primary=True),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_placement_belongs_to_exactly_one_kind_of_item(self, session):
        a = await locs.create_location(session, "A", None)
        await _material(session, qty=1)
        await _product(session)
        from sqlalchemy.exc import IntegrityError

        session.add(StockPlacement(location_id=a.id, material_id=1, product_id=1, qty=1))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


class TestRouter:
    """The HTTP layer: the shapes the frontend reads."""

    async def test_the_tree_endpoint_reports_counts_and_unplaced_items(self, session):
        from app.routers import locations as router
        from app.schemas.location import LocationCreate, RangeRequest

        room = await router.create_location(LocationCreate(name="Workshop"), session)
        bins = await router.generate_range(RangeRequest(parent_id=room.id, pattern="Bin {1-3}"), session)
        item = await _material(session, qty=10)
        await placements.place(session, item, bins[0].id, Decimal(4))
        await _material(session, 2, "Wicks", MaterialUnit.each, qty=5)

        tree = await router.list_locations(session)
        by_name = {r.name: r for r in tree.locations}
        assert [r.name for r in bins] == ["Bin 1", "Bin 2", "Bin 3"]
        assert by_name["Workshop"].rolled_up_items == 1 and by_name["Bin 1"].own_items == 1
        assert tree.total_locations == 4 and tree.empty_locations == 2  # Bin 2 and Bin 3
        assert tree.unplaced_items == 1 and tree.placeable_items == 2

    async def test_preview_marks_what_exists_and_reports_errors_without_raising(self, session):
        from app.routers import locations as router
        from app.schemas.location import RangeRequest

        await locs.create_location(session, "Box02", None)
        ok = await router.preview_range(RangeRequest(pattern="Box{01-03}"), session)
        assert [(n.name, n.exists) for n in ok.names] == [("Box01", False), ("Box02", True), ("Box03", False)]
        assert ok.new_count == 2 and ok.error is None
        bad = await router.preview_range(RangeRequest(pattern="Box{x}"), session)
        assert bad.names == [] and "can’t read" in bad.error

    async def test_detail_lists_contents_history_and_the_path(self, session):
        from app.routers import locations as router

        shelf = await locs.create_location(session, "Shelf", None)
        box = await locs.create_location(session, "Box", shelf.id)
        item = await _material(session, qty=10)
        await placements.place(session, item, box.id, Decimal(10))
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-2), "used")

        detail = await router.get_location(box.id, session)
        assert [p.name for p in detail.path] == ["Shelf", "Box"]
        assert [(c.name, c.qty, c.is_primary) for c in detail.contents] == [("Soy wax", Decimal(8), True)]
        assert detail.history_count == 2
        assert {h.kind for h in detail.history} == {PlacementEventKind.place, PlacementEventKind.auto_deduct}
        assert all(h.item_name == "Soy wax" for h in detail.history)

    async def test_patch_renames_and_moves_and_can_move_to_the_top_level(self, session):
        from app.routers import locations as router
        from app.schemas.location import LocationUpdate

        a = await locs.create_location(session, "A", None)
        b = await locs.create_location(session, "B", a.id)
        moved = await router.update_location(b.id, LocationUpdate(name="B2", parent_id=None), session)
        assert (moved.name, moved.parent_id) == ("B2", None)
        back = await router.update_location(b.id, LocationUpdate(parent_id=a.id), session)
        assert back.parent_id == a.id and back.name == "B2"

    async def test_summary_and_stored_in_agree(self, session):
        from app.routers import locations as router

        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=10)
        await placements.place(session, item, shelf.id, Decimal(6))
        view = await router.stored_in(1, None, None, session)
        assert (view.placed, view.unassigned, view.follows_stock) == (Decimal(6), Decimal(4), True)
        (summary,) = await router.item_summaries(session)
        assert (summary.name, summary.primary_path, summary.unit) == ("Soy wax", ["Shelf"], "g")


class TestCsv:
    """Locations as a spreadsheet: path is the identity, missing parents are made on the way."""

    async def _import(self, session, text):
        from app.services.locations_csv import import_locations_csv

        return await import_locations_csv(session, text.encode("utf-8-sig"))

    async def test_export_lists_every_location_by_path_in_natural_order(self, session):
        from app.services.locations_csv import export_locations_csv

        shop = await locs.create_location(session, "Workshop", None, "Main room")
        await locs.generate_range(session, shop.id, "Bin {1-3}")
        await locs.create_location(session, "Bin 10", shop.id)
        old = await locs.create_location(session, "Old shed", None)
        await locs.set_archived(session, old.id, True)

        lines = (await export_locations_csv(session)).splitlines()
        assert lines[0] == "path,code,notes,archived"
        assert [l.split(",")[0] for l in lines[1:]] == [
            "Old shed",
            "Workshop",
            "Workshop > Bin 1",
            "Workshop > Bin 2",
            "Workshop > Bin 3",
            "Workshop > Bin 10",
        ]
        assert lines[2].endswith(",Main room,")
        assert lines[1].endswith(",yes")

    async def test_import_creates_missing_parents_and_counts_every_location_made(self, session):
        result = await self._import(session, "path\nWorkshop > Oils rack > Bin 1\nWorkshop > Oils rack > Bin 2\n")
        assert (result["created"], result["updated"], result["failed"]) == (4, 0, [])  # Workshop, Oils rack, Bin 1, Bin 2
        nodes = await locs.load_nodes(session)
        paths = sorted(locs.display_path(nodes, i) for i in nodes)
        assert paths == ["Workshop", "Workshop › Oils rack", "Workshop › Oils rack › Bin 1", "Workshop › Oils rack › Bin 2"]

    async def test_import_reads_the_arrow_the_app_displays_as_well_as_a_plain_one(self, session):
        await self._import(session, "path\nA › B\nA>C\n")
        nodes = await locs.load_nodes(session)
        assert sorted(locs.display_path(nodes, i) for i in nodes) == ["A", "A › B", "A › C"]

    async def test_importing_what_exists_changes_nothing_and_updates_notes(self, session):
        await self._import(session, "path,notes\nShelf,\n")
        again = await self._import(session, "path,notes\nshelf,Top shelf\nShelf,Top shelf\n")
        assert (again["created"], again["updated"]) == (0, 1)  # the second row changes nothing
        assert (await session.scalars(select(Location.notes))).first() == "Top shelf"
        blank = await self._import(session, "path,notes\nShelf,\n")
        assert blank["updated"] == 0  # a blank cell never wipes a note

    async def test_a_round_trip_reproduces_the_layout(self, session):
        from app.services.locations_csv import export_locations_csv

        shop = await locs.create_location(session, "Workshop", None, "Main room")
        await locs.generate_range(session, shop.id, "Bin {1-2}")
        exported = await export_locations_csv(session)
        again = await self._import(session, exported)
        assert (again["created"], again["updated"], again["failed"]) == (0, 0, [])

    async def test_bad_rows_are_reported_and_the_rest_still_import(self, session):
        text = "path\nGood\nA > > B\n" + "x" * 101 + "\nAlso good\n"
        result = await self._import(session, text)
        assert result["created"] == 2
        assert [f["row"] for f in result["failed"]] == [3, 4]
        assert "empty part" in result["failed"][0]["error"]
        assert "at most 100" in result["failed"][1]["error"]

    async def test_a_row_that_fails_part_way_leaves_nothing_behind(self, session):
        long = "y" * 101
        result = await self._import(session, f"path\nRoom > {long}\n")
        assert result["created"] == 0 and len(result["failed"]) == 1
        assert (await session.scalars(select(Location))).all() == []  # not even "Room"

    async def test_blank_lines_are_ignored_and_a_path_column_is_required(self, session):
        assert (await self._import(session, "path\n\nShelf\n\n"))["created"] == 1
        with pytest.raises(HTTPException) as exc:
            await self._import(session, "name\nShelf\n")
        assert exc.value.status_code == 400

    async def test_archiving_through_the_file_follows_the_same_rules_as_the_page(self, session):
        shelf = await locs.create_location(session, "Shelf", None)
        item = await _material(session, qty=3)
        await placements.place(session, item, shelf.id, Decimal(3))
        await locs.create_location(session, "Empty one", None)

        result = await self._import(session, "path,archived\nShelf,yes\nEmpty one,yes\n")
        assert result["updated"] == 1
        assert [f["row"] for f in result["failed"]] == [2]
        assert "holds stock" in result["failed"][0]["error"]
        restored = await self._import(session, "path,archived\nEmpty one,no\n")
        assert restored["updated"] == 1

    async def test_nothing_can_be_added_inside_an_archived_location(self, session):
        old = await locs.create_location(session, "Old shed", None)
        await locs.set_archived(session, old.id, True)
        result = await self._import(session, "path\nOld shed > Crate\n")
        assert result["created"] == 0 and "archived" in result["failed"][0]["error"]

    async def test_the_endpoints_are_declared_before_the_id_route(self, session):
        from app.routers import locations as router

        paths = [r.path for r in router.router.routes]
        assert "/locations/export" in paths and "/locations/import" in paths
        # Declared after "/{location_id}", "export" would be read as an id and refused.
        assert paths.index("/locations/export") < paths.index("/locations/{location_id}")
