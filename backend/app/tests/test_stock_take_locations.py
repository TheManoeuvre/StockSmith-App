"""A stock take scoped by location (docs/plan-locations.md §7.1).

One line per item per place. Approving a line sets that place to the counted figure, and the item's
total moves only by what the count proves: found stock comes out of Unassigned first, and a partial
count of an item kept in several places never dates it.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.location import PlacementEvent, PlacementEventKind, StockPlacement
from app.models.material import LegacyMaterialCategory, Material, MaterialAdjustmentMode, MaterialUnit
from app.models.product import Product
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus
from app.schemas.stock_take import BulkLineCount, StockTakeScope
from app.services import locations as locs
from app.services import placements
from app.services import stock_take_locations as place_counts
from app.services import stock_takes
from app.services.costing import create_adjustment
from app.services.placement_hooks import ItemRef

_FRESH = {"populate_existing": True}


def scope(location_id, **kw):
    return StockTakeScope(include_materials=True, include_products=True, location_id=location_id, **kw)


async def _material(session, mid=1, name="Soy wax", unit=MaterialUnit.g, qty=0):
    session.add(Material(id=mid, name=name, category=LegacyMaterialCategory.other, unit=unit))
    await session.commit()
    if qty:
        await create_adjustment(session, mid, MaterialAdjustmentMode.adjust, Decimal(qty), "opening")
    return ItemRef(material_id=mid)


async def _product(session, pid=1, stock=0, allocated=0, name="Candle"):
    session.add(Product(id=pid, name=name, sku=f"P-{pid}", current_stock=stock, allocated_qty=allocated))
    await session.commit()
    return ItemRef(product_id=pid)


async def _material_row(session, mid=1):
    return (await session.scalars(select(Material).where(Material.id == mid).execution_options(**_FRESH))).one()


async def _total(session, ref):
    if ref.material_id is not None:
        return Decimal((await _material_row(session, ref.material_id)).current_qty)
    row = (await session.scalars(select(Product).where(Product.id == ref.product_id).execution_options(**_FRESH))).one()
    return Decimal(row.current_stock)


async def _here(session, ref, location_id):
    rows = (await session.scalars(select(StockPlacement).where(*ref.owner_filters()).execution_options(**_FRESH))).all()
    return {r.location_id: Decimal(r.qty) for r in rows}.get(location_id)


async def _lines(session, take_id):
    return list(
        await session.scalars(
            select(StockTakeLine).where(StockTakeLine.stock_take_id == take_id).order_by(StockTakeLine.id).execution_options(**_FRESH)
        )
    )


async def _line(session, take_id, ref, location_id=None):
    for l in await _lines(session, take_id):
        if (l.material_id, l.product_id, l.variant_id) == (ref.material_id, ref.product_id, ref.variant_id) and (
            location_id is None or l.location_id == location_id
        ):
            return l
    raise AssertionError("no such line")


async def _count(session, take_id, ref, qty, location_id=None):
    line = await _line(session, take_id, ref, location_id)
    await stock_takes.set_line_count(session, take_id, line.id, Decimal(qty), None)
    return line


async def _take(session, location_id, **kw):
    take, _ = await stock_takes.create_stock_take(session, scope(location_id, **kw))
    return take.id


@pytest.fixture
async def shelf(session):
    """Workshop > Shelf, plus a Loft elsewhere. Returns (workshop, shelf, loft) ids."""
    workshop = await locs.create_location(session, "Workshop", None)
    shelf = await locs.create_location(session, "Shelf", workshop.id)
    loft = await locs.create_location(session, "Loft", None)
    return workshop.id, shelf.id, loft.id


class TestScope:
    async def test_one_line_per_item_per_place_in_the_location_and_inside_it(self, session, shelf):
        workshop, shelf_id, loft = shelf
        wax, wick = await _material(session, 1, qty=30), await _material(session, 2, "Wicks", MaterialUnit.each, qty=20)
        await placements.place(session, wax, workshop, Decimal(10))
        await placements.place(session, wax, shelf_id, Decimal(20))
        await placements.place(session, wick, loft, Decimal(20))

        take_id = await _take(session, workshop)
        lines = await _lines(session, take_id)

        assert sorted((l.location_label, Decimal(l.expected_qty)) for l in lines) == [
            ("Workshop", 10),
            ("Workshop › Shelf", 20),
        ]  # the same item twice, once per place; the Loft is outside the walk
        assert (await session.get(StockTake, take_id)).location_id == workshop

    async def test_the_sheet_is_arranged_by_place(self, session, shelf):
        workshop, shelf_id, _ = shelf
        bins = await locs.generate_range(session, shelf_id, "Bin {1-10}")
        wax = await _material(session, 1, qty=30)
        for b in (bins[9], bins[1], bins[0]):  # placed out of order on purpose
            await placements.place(session, wax, b.id, Decimal(10))
        take_id = await _take(session, workshop)
        lines = await _lines(session, take_id)
        grouped = stock_takes.group_lines(lines, {1: await _material_row(session)}, {}, {})
        assert [g.group for g in grouped] == [
            "Workshop › Shelf › Bin 1",
            "Workshop › Shelf › Bin 2",
            "Workshop › Shelf › Bin 10",
        ]
        assert {g.section for g in grouped} == {"Location"}

    async def test_the_item_filters_still_apply(self, session, shelf):
        workshop, shelf_id, _ = shelf
        wax = await _material(session, 1, qty=10)
        candle = await _product(session, stock=5)
        await placements.place(session, wax, shelf_id, Decimal(10))
        await placements.place(session, candle, shelf_id, Decimal(5))
        take, _ = await stock_takes.create_stock_take(
            session, StockTakeScope(include_products=True, location_id=workshop)
        )
        assert [(l.product_id, l.material_id) for l in await _lines(session, take.id)] == [(1, None)]

    async def test_an_empty_location_can_still_be_counted(self, session, shelf):
        take_id = await _take(session, shelf[1])
        assert await _lines(session, take_id) == []
        with pytest.raises(HTTPException, match="Nothing to count"):
            await stock_takes.create_stock_take(session, StockTakeScope(include_materials=True))

    async def test_an_archived_location_cannot_be_counted(self, session, shelf):
        await locs.set_archived(session, shelf[2], True)
        with pytest.raises(HTTPException, match="archived"):
            await stock_takes.create_stock_take(session, scope(shelf[2]))

    async def test_the_description_names_the_place(self, session, shelf):
        preview = await stock_takes.preview_scope(session, scope(shelf[1]))
        assert preview.scope_description.endswith("in Workshop › Shelf")

    async def test_item_scope_takes_are_untouched(self, session, shelf):
        wax = await _material(session, 1, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take, _ = await stock_takes.create_stock_take(session, StockTakeScope(include_materials=True))
        line = (await _lines(session, take.id))[0]
        assert line.location_id is None and line.location_label is None and not line.found


class TestApplying:
    async def test_a_full_count_of_the_only_placement_sets_the_total_and_dates_the_item(self, session, shelf):
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 7)

        result = await stock_takes.approve_stock_take(session, take_id)

        assert (result.applied, result.conflicts, result.skipped) == (1, 0, 0)
        assert await _total(session, wax) == 7 and await _here(session, wax, shelf[1]) == 7
        assert (await _material_row(session)).last_stock_take_at is not None
        events = (await session.scalars(select(PlacementEvent).where(PlacementEvent.kind == PlacementEventKind.count))).all()
        assert len(events) == 1 and events[0].from_location_id == shelf[1]
        # One history row, not a count plus an automatic deduction.
        assert (await session.scalars(select(PlacementEvent).where(PlacementEvent.kind == PlacementEventKind.auto_deduct))).all() == []
        assert (await session.scalars(select(StockTake).execution_options(**_FRESH))).one().status is StockTakeStatus.closed

    async def test_a_partial_count_of_an_item_in_two_places_moves_only_its_own_place_and_does_not_date_it(self, session, shelf):
        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))
        await placements.place(session, wax, shelf[2], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 15)

        await stock_takes.approve_stock_take(session, take_id)

        assert await _total(session, wax) == 25  # the 5 that weren't there are gone
        assert await _here(session, wax, shelf[1]) == 15
        assert await _here(session, wax, shelf[2]) == 10  # the other place is untouched
        assert (await _material_row(session)).last_stock_take_at is None

    async def test_one_item_counted_in_two_places_in_the_same_take(self, session, shelf):
        workshop, shelf_id, _ = shelf
        wax = await _material(session, qty=30)
        await placements.place(session, wax, workshop, Decimal(10))
        await placements.place(session, wax, shelf_id, Decimal(20))
        take_id = await _take(session, workshop)
        await _count(session, take_id, wax, 8, workshop)
        await _count(session, take_id, wax, 22, shelf_id)

        result = await stock_takes.approve_stock_take(session, take_id)

        assert (result.applied, result.conflicts) == (2, 0)
        assert await _total(session, wax) == 30  # 2 short in one place, 2 over in the other
        assert (await _here(session, wax, workshop), await _here(session, wax, shelf_id)) == (8, 22)

    async def test_more_than_expected_is_taken_from_unassigned_first(self, session, shelf):
        wax = await _material(session, qty=100)
        await placements.place(session, wax, shelf[1], Decimal(30))  # 70 never placed
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 50)

        await stock_takes.approve_stock_take(session, take_id)

        assert await _total(session, wax) == 100  # the 20 were the unplaced stock, not new stock
        assert await _here(session, wax, shelf[1]) == 50

    async def test_only_the_excess_over_unassigned_raises_the_total(self, session, shelf):
        wax = await _material(session, qty=40)
        await placements.place(session, wax, shelf[1], Decimal(30))  # 10 unassigned
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 55)

        await stock_takes.approve_stock_take(session, take_id)

        assert await _total(session, wax) == 55 and await _here(session, wax, shelf[1]) == 55

    async def test_counting_zero_removes_the_placement_and_hands_the_primary_on(self, session, shelf):
        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))  # primary
        await placements.place(session, wax, shelf[2], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 0)

        await stock_takes.approve_stock_take(session, take_id)

        assert await _here(session, wax, shelf[1]) is None
        stored = await placements.stored_in(session, wax)
        assert [(p.location_id, p.is_primary) for p in stored.placements] == [(shelf[2], True)]
        assert await _total(session, wax) == 10

    async def test_a_confirming_count_changes_nothing_but_still_dates_a_full_count(self, session, shelf):
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 10)
        await stock_takes.approve_stock_take(session, take_id)
        assert await _total(session, wax) == 10
        assert (await _material_row(session)).last_stock_take_at is not None

    async def test_blank_lines_are_skipped_and_leave_everything_alone(self, session, shelf):
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        result = await stock_takes.approve_stock_take(session, take_id)
        assert (result.applied, result.skipped) == (0, 1)
        assert await _total(session, wax) == 10
        assert (await _material_row(session)).last_stock_take_at is None

    async def test_finished_goods_count_in_whole_units(self, session, shelf):
        candle = await _product(session, stock=12)
        await placements.place(session, candle, shelf[1], Decimal(12))
        take_id = await _take(session, shelf[1])
        line = await _line(session, take_id, candle)
        with pytest.raises(HTTPException, match="whole number"):
            await stock_takes.set_line_count(session, take_id, line.id, Decimal("3.5"), None)
        await stock_takes.set_line_count(session, take_id, line.id, Decimal(9), None)
        await stock_takes.approve_stock_take(session, take_id)
        assert await _total(session, candle) == 9 and await _here(session, candle, shelf[1]) == 9

    async def test_approving_twice_does_not_apply_twice(self, session, shelf):
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 6)
        await stock_takes.approve_stock_take(session, take_id)
        await stock_takes.approve_stock_take(session, take_id)
        assert await _total(session, wax) == 6

    async def test_the_adjustment_reason_names_the_take_and_the_place(self, session, shelf):
        from app.models.material import MaterialAdjustment

        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))
        await placements.place(session, wax, shelf[2], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 15)
        await stock_takes.approve_stock_take(session, take_id)
        reasons = (await session.scalars(select(MaterialAdjustment.reason))).all()
        assert f"Stock take #{take_id} at Workshop › Shelf" in reasons


class TestConflicts:
    async def test_stock_that_moved_since_the_take_started_is_flagged_not_applied(self, session, shelf):
        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 18)
        await placements.move_item(session, wax, shelf[1], shelf[2], Decimal(5))

        result = await stock_takes.approve_stock_take(session, take_id)

        assert result.conflicts == 1
        line = await _line(session, take_id, wax)
        assert line.status is StockTakeLineStatus.conflict and "was 20, now 15" in line.conflict_reason
        assert await _here(session, wax, shelf[1]) == 15 and await _total(session, wax) == 30

    async def test_use_my_count_rebaselines_against_what_is_there_now(self, session, shelf):
        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))
        take_id = await _take(session, shelf[1])
        line = await _count(session, take_id, wax, 18)
        await placements.move_item(session, wax, shelf[1], shelf[2], Decimal(5))
        await stock_takes.approve_stock_take(session, take_id)

        await stock_takes.resolve_line(session, take_id, line.id, "accept_counted")

        assert await _here(session, wax, shelf[1]) == 18  # 15 after the move, so 3 more were found
        assert (await _line(session, take_id, wax)).status is StockTakeLineStatus.applied

    async def test_leave_it_keeps_what_the_system_says(self, session, shelf):
        wax = await _material(session, qty=30)
        await placements.place(session, wax, shelf[1], Decimal(20))
        take_id = await _take(session, shelf[1])
        line = await _count(session, take_id, wax, 18)
        await placements.move_item(session, wax, shelf[1], shelf[2], Decimal(5))
        await stock_takes.approve_stock_take(session, take_id)
        await stock_takes.resolve_line(session, take_id, line.id, "accept_system")
        assert (await _line(session, take_id, wax)).status is StockTakeLineStatus.accepted_system
        assert await _here(session, wax, shelf[1]) == 15

    async def test_a_short_count_with_units_allocated_to_orders_is_flagged(self, session, shelf):
        candle = await _product(session, stock=10, allocated=5)
        await placements.place(session, candle, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, candle, 5)  # just the loose ones: the boxed 5 aren't on the shelf
        await stock_takes.approve_stock_take(session, take_id)
        line = await _line(session, take_id, candle)
        assert line.status is StockTakeLineStatus.conflict and "allocated to open orders" in line.conflict_reason
        assert await _total(session, candle) == 10

    async def test_one_flagged_line_does_not_stop_the_others(self, session, shelf):
        wax = await _material(session, 1, qty=20)
        wick = await _material(session, 2, "Wicks", MaterialUnit.each, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(20))
        await placements.place(session, wick, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 15)
        await _count(session, take_id, wick, 8)
        await placements.move_item(session, wax, shelf[1], shelf[2], Decimal(1))
        result = await stock_takes.approve_stock_take(session, take_id)
        assert (result.applied, result.conflicts) == (1, 1)
        assert await _total(session, wick) == 8


class TestFoundItems:
    async def test_an_item_found_where_it_was_not_expected_is_placed_and_counted(self, session, shelf):
        wax = await _material(session, qty=0)
        take_id = await _take(session, shelf[0])
        take = await session.get(StockTake, take_id)
        line = await place_counts.add_found(session, take, shelf[1], wax, None, None)
        await stock_takes.set_line_count(session, take_id, line.id, Decimal(12), None)

        assert line.found and Decimal(line.expected_qty) == 0 and line.location_label == "Workshop › Shelf"
        await stock_takes.approve_stock_take(session, take_id)

        assert await _here(session, wax, shelf[1]) == 12
        assert await _total(session, wax) == 12  # nothing was unassigned, so it is new stock

    async def test_found_stock_comes_out_of_unassigned_first(self, session, shelf):
        wax = await _material(session, qty=40)
        take_id = await _take(session, shelf[0])
        line = await place_counts.add_found(session, await session.get(StockTake, take_id), shelf[1], wax, None, None)
        await stock_takes.set_line_count(session, take_id, line.id, Decimal(12), None)
        await stock_takes.approve_stock_take(session, take_id)
        assert await _here(session, wax, shelf[1]) == 12 and await _total(session, wax) == 40

    async def test_it_has_to_be_inside_the_location_being_counted(self, session, shelf):
        wax = await _material(session, qty=5)
        take = await session.get(StockTake, await _take(session, shelf[1]))
        with pytest.raises(HTTPException, match="inside the location"):
            await place_counts.add_found(session, take, shelf[2], wax, None, None)

    async def test_adding_an_item_that_already_has_a_line_returns_that_line(self, session, shelf):
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        before = await _line(session, take_id, wax)
        again = await place_counts.add_found(session, await session.get(StockTake, take_id), shelf[1], wax, None, None)
        assert again.id == before.id and not again.found
        assert len(await _lines(session, take_id)) == 1

    async def test_an_item_scope_take_cannot_have_items_added(self, session, shelf):
        await _material(session, qty=10)
        take, _ = await stock_takes.create_stock_take(session, StockTakeScope(include_materials=True))
        with pytest.raises(HTTPException, match="scoped by location"):
            await place_counts.add_found(session, take, shelf[1], ItemRef(material_id=1), None, None)


class TestAutomaticFollowing:
    async def test_a_single_placement_still_follows_the_stock_outside_a_count(self, session, shelf):
        """The take pauses automatic following only while it applies."""
        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        take_id = await _take(session, shelf[1])
        await _count(session, take_id, wax, 8)
        await stock_takes.approve_stock_take(session, take_id)
        await create_adjustment(session, 1, MaterialAdjustmentMode.adjust, Decimal(-3), "breakage")
        assert await _here(session, wax, shelf[1]) == 5


class TestRouter:
    async def test_the_endpoints_carry_the_place(self, session, shelf):
        from app.routers import stock_takes as router
        from app.schemas.stock_take import FoundLine

        wax = await _material(session, qty=10)
        await placements.place(session, wax, shelf[1], Decimal(10))
        created = await router.create_stock_take(scope(shelf[0]), session)
        take_id = created.stock_take.id
        detail = await router.get_stock_take(take_id, session)
        assert detail.location_id == shelf[0]
        assert [(l.name, l.section, l.group, l.found) for l in detail.lines] == [
            ("Soy wax", "Location", "Workshop › Shelf", False)
        ]

        await _material(session, 2, "Wicks", MaterialUnit.each, qty=0)
        after = await router.add_found_line(
            take_id, FoundLine(material_id=2, location_id=shelf[1], counted_qty=Decimal(4)), session
        )
        found = next(l for l in after.lines if l.name == "Wicks")
        assert found.found and found.counted_qty == 4 and found.location_label == "Workshop › Shelf"
