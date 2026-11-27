"""The Work list: what is outstanding, derived from orders, builds, stock and recorded progress.

Scenarios, not unit tests of helpers — each one builds a small shop (products, variants, a BOM,
orders, builds) and asserts what the seller would see. The ones that matter most are the ones the
plan promised: pack sizes scale the prep, prep can run ahead of orders, mixing is opt-in and only
across the same materials, a personalised file gates making, finishing gates shipping, and an order
ships whole.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.material import LegacyMaterialCategory, Material, MaterialAdjustment, MaterialUnit
from app.models.order import Order
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.models.work import WorkQtyBasis, WorkStage, WorkStageKind
from app.routers.orders import create_order
from app.schemas.order import OrderCreate, OrderLineInput
from app.services import material_categories, work, work_routes
from app.services.builds import create_build
from app.services.costing import recompute_material
from app.services.work_routes import StepInput, WorkConfigError


@pytest_asyncio.fixture
async def shop(session, pushes):
    """Work flow switched on, with the first-enabled time safely in the past so builds made in
    these tests (stamped to the second by the database) always count as made."""
    await work_routes.ensure_work_defaults(session)
    await work_routes.set_work_flow_enabled(session, True)
    settings = await work_routes.get_work_settings(session)
    settings.work_flow_enabled_at = datetime.now(timezone.utc) - timedelta(days=1)
    await session.commit()
    return session


async def _builtin(session, kind):
    return (await session.execute(select(WorkStage).where(WorkStage.kind == kind))).scalar_one()


async def _stage(session, name, **kw):
    return await work_routes.create_stage(
        session,
        name=name,
        station=kw.get("station"),
        per_order=kw.get("per_order", False),
        wait_minutes=None,
        typical_minutes=None,
    )


async def _material(session, name, category, qty=Decimal(100000)):
    cat = await material_categories.find_or_create(session, category)
    m = Material(name=name, category=LegacyMaterialCategory.filament, category_id=cat.id, unit=MaterialUnit.g)
    session.add(m)
    await session.flush()
    session.add(MaterialAdjustment(material_id=m.id, qty_delta=qty, reason="seed"))
    await recompute_material(session, m.id)
    return m


async def _set_route(session, product, name, *steps):
    route = await work_routes.create_route(session, name=name, steps=list(steps))
    product.work_route_id = route.id
    await session.commit()
    return route


async def _product(session, name="Coaster", sku="C-1", stock=0, filament=None, bom=()):
    p = Product(name=name, sku=sku, current_stock=stock, allocated_qty=0)
    session.add(p)
    await session.flush()
    if filament is not None:
        session.add(ProductMaterial(product_id=p.id, material_id=filament.id, qty_required=Decimal(28)))
    for material, qty in bom:
        session.add(ProductMaterial(product_id=p.id, material_id=material.id, qty_required=Decimal(qty)))
    await session.commit()
    return p


async def _variant(session, product, name, stock=0, overrides=()):
    v = ProductVariant(product_id=product.id, variant_name=name, current_stock=stock, allocated_qty=0)
    session.add(v)
    await session.flush()
    for material, qty in overrides:
        session.add(ProductVariantMaterial(variant_id=v.id, material_id=material.id, qty_required=Decimal(qty)))
    await session.commit()
    return v


async def _order(session, product, qty, variant=None, due_in_days=None):
    read = await create_order(
        OrderCreate(
            lines=[OrderLineInput(product_id=product.id, variant_id=variant.id if variant else None, ordered_qty=qty, unit_price=Decimal("5"))]
        ),
        session=session,
    )
    if due_in_days is not None:
        order = await session.get(Order, read.id)
        order.ship_by_date = datetime.now(timezone.utc) + timedelta(days=due_in_days)
        await session.commit()
    return read.id


async def _build(session, product, qty, variant=None):
    await create_build(session, product.id, variant.id if variant else None, qty, None)
    await session.commit()


def _cards(listing, stage_name=None):
    return [c for c in listing.cards if stage_name is None or c.stage_name == stage_name]


# ---- off / baseline ---------------------------------------------------------------------------


async def test_switched_off_there_is_nothing(session):
    await work_routes.ensure_work_defaults(session)
    product = await _product(session)
    await _order(session, product, 2)
    listing = await work.compute_work_list(session)
    assert listing.enabled is False and listing.cards == [] and listing.ship_cards == []


async def test_make_then_ship_on_the_default_route(shop):
    filament = await _material(shop, "PLA", "filament")
    product = await _product(shop, filament=filament)
    order_id = await _order(shop, product, 3)

    listing = await work.compute_work_list(shop)
    [make] = _cards(listing, "Make")
    assert make.qty == 3 and make.order_ids == [order_id] and make.can_record is False
    assert listing.ship_cards == []
    assert [o.order_id for o in listing.waiting_orders] == [order_id]
    assert "still to make" in listing.waiting_orders[0].waiting_for[0]

    await _build(shop, product, 3)
    listing = await work.compute_work_list(shop)
    assert _cards(listing) == []
    assert [o.order_id for o in listing.ship_cards] == [order_id]
    assert listing.waiting_orders == []


async def test_stock_on_hand_skips_the_route_entirely(shop):
    product = await _product(shop, stock=5)
    mask = await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    await _set_route(shop, product, "Prep", StepInput(stage_id=mask.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id))
    order_id = await _order(shop, product, 2)
    listing = await work.compute_work_list(shop)
    assert listing.cards == []
    assert [o.order_id for o in listing.ship_cards] == [order_id]


async def test_a_partly_covered_line_only_makes_the_shortfall(shop):
    filament = await _material(shop, "PLA", "filament")
    product = await _product(shop, stock=1, filament=filament)
    await _order(shop, product, 4)
    [make] = _cards(await work.compute_work_list(shop), "Make")
    assert make.qty == 3


async def test_builds_before_the_feature_was_switched_on_do_not_count(session, pushes):
    filament = await _material(session, "PLA", "filament")
    mask = await _stage(session, "Finish")
    await work_routes.ensure_work_defaults(session)
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    product = await _product(session, filament=filament)
    await _set_route(session, product, "Finish route", StepInput(stage_id=make.id), StepInput(stage_id=mask.id), StepInput(stage_id=ship.id))
    await _build(session, product, 5)  # made before the seller started using the feature
    await work_routes.set_work_flow_enabled(session, True)
    settings = await work_routes.get_work_settings(session)
    settings.work_flow_enabled_at = datetime.now(timezone.utc) + timedelta(minutes=5)  # "now" for this test
    await session.commit()
    await _order(session, product, 2)
    listing = await work.compute_work_list(session)
    assert _cards(listing, "Finish") == []  # no ghost finishing for stock that predates the feature


# ---- preparation, pack sizes, prep-ahead ------------------------------------------------------


async def _coaster_world(shop):
    """Ceramic coaster: clean (per item) -> mask (per blank) -> make -> ship, in 2- and 6-packs."""
    filament = await _material(shop, "PLA", "filament")
    blanks = await _material(shop, "Ceramic blank", "blanks")
    blank_category = blanks.category_id
    clean, mask = await _stage(shop, "Pre-clean"), await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, filament=filament, bom=[(blanks, 1)])
    two = await _variant(shop, product, "2 pack", overrides=[(blanks, 2)])
    six = await _variant(shop, product, "6 pack", overrides=[(blanks, 6)])
    await _set_route(
        shop,
        product,
        "Ceramic coaster",
        StepInput(stage_id=clean.id, qty_basis=WorkQtyBasis.bom_category, qty_category_id=blank_category),
        StepInput(stage_id=mask.id, qty_basis=WorkQtyBasis.bom_category, qty_category_id=blank_category),
        StepInput(stage_id=make.id),
        StepInput(stage_id=ship.id),
    )
    return product, two, six, clean, mask


async def test_pack_size_scales_the_prep(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    await _order(shop, product, 1, variant=six)
    [card] = _cards(await work.compute_work_list(shop), "Pre-clean")
    assert card.qty == 6 and card.unit_label == "blanks" and card.parts[0].finished_units == 1

    await _order(shop, product, 1, variant=two)
    cards = _cards(await work.compute_work_list(shop), "Pre-clean")
    assert sorted(c.qty for c in cards) == [2, 6]  # not mixed: one card per variant


async def test_a_stage_waits_for_the_one_before_it(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    await _order(shop, product, 1, variant=six)
    assert _cards(await work.compute_work_list(shop), "Mask") == []  # nothing cleaned yet

    await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=clean.id, qty=6)
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Pre-clean") == []
    [mask_card] = _cards(listing, "Mask")
    assert mask_card.qty == 6


async def test_partial_progress_counts_and_completes_into_make(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    await _order(shop, product, 1, variant=six)
    await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=clean.id, qty=6)
    await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=mask.id, qty=4)
    listing = await work.compute_work_list(shop)
    [mask_card] = _cards(listing, "Mask")
    assert mask_card.qty == 2  # 4 of 6 masked
    assert _cards(listing, "Make") == []  # a 6-pack isn't makeable on 4 masked blanks

    await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=mask.id, qty=2)
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Mask") == []
    [make] = _cards(listing, "Make")
    assert make.qty == 1


async def test_prep_ahead_of_any_order_is_used_by_the_next_one(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    for stage in (clean, mask):
        await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=stage.id, qty=6)
    assert (await work.compute_work_list(shop)).cards == []  # nothing needed yet, nothing nagging

    await _order(shop, product, 1, variant=six)
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Pre-clean") == [] and _cards(listing, "Mask") == []
    [make] = _cards(listing, "Make")
    assert make.qty == 1


async def test_prep_survives_a_cancelled_order(shop):
    """Prep is pooled, not tied to the order it was done for, so cancelling loses nothing."""
    product, two, six, clean, mask = await _coaster_world(shop)
    order_id = await _order(shop, product, 1, variant=six)
    for stage in (clean, mask):
        await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=stage.id, qty=6)
    order = await session_get_order(shop, order_id)
    order.status = order.status.cancelled
    await shop.commit()
    await _order(shop, product, 1, variant=six)
    listing = await work.compute_work_list(shop)
    assert [c.stage_name for c in listing.cards] == ["Make"]


async def session_get_order(session, order_id):
    return await session.get(Order, order_id)


async def test_ticking_a_later_stage_first_leaves_no_ghost_work(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    await _order(shop, product, 1, variant=six)
    await work.record_progress(shop, product_id=product.id, variant_id=six.id, stage_id=mask.id, qty=6)  # forgot to tick clean
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Pre-clean") == []
    assert [c.stage_name for c in listing.cards] == ["Make"]


async def test_building_without_ticking_prep_clears_the_prep(shop):
    product, two, six, clean, mask = await _coaster_world(shop)
    await _order(shop, product, 1, variant=six)
    await _build(shop, product, 1, variant=six)
    listing = await work.compute_work_list(shop)
    assert listing.cards == []
    assert len(listing.ship_cards) == 1


# ---- batching and mixing ----------------------------------------------------------------------


async def _two_designs(shop, *, mix: bool, same_filament: bool = True):
    pla = await _material(shop, "PLA Black", "filament")
    petg = pla if same_filament else await _material(shop, "PETG Black", "filament")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, name="Printed coaster", sku="P-1", filament=pla)
    design_a = await _variant(shop, product, "Design A")
    design_b = await _variant(shop, product, "Design B", overrides=[] if same_filament else [(petg, 28)])
    if not same_filament:
        # Substitute the filament on design B so its resolved BOM prints from different material.
        row = ProductVariantMaterial(variant_id=design_b.id, material_id=petg.id, qty_required=Decimal(28), replaces_material_id=pla.id)
        shop.add(row)
        await shop.commit()
    await _set_route(shop, product, "Printed", StepInput(stage_id=make.id, mix_variants=mix), StepInput(stage_id=ship.id))
    await _order(shop, product, 3, variant=design_a, due_in_days=2)
    await _order(shop, product, 3, variant=design_b, due_in_days=1)
    return product


async def test_variants_stay_separate_unless_mixing_is_on(shop):
    await _two_designs(shop, mix=False)
    makes = _cards(await work.compute_work_list(shop), "Make")
    assert len(makes) == 2 and all(len(c.parts) == 1 for c in makes)


async def test_mixing_merges_variants_that_print_from_the_same_materials(shop):
    await _two_designs(shop, mix=True)
    [make] = _cards(await work.compute_work_list(shop), "Make")
    assert make.qty == 6 and make.batch_note == "Mixed batch"
    assert sorted(p.variant_name for p in make.parts) == ["Design A", "Design B"]
    assert len(make.order_ids) == 2


async def test_mixing_never_merges_different_materials(shop):
    await _two_designs(shop, mix=True, same_filament=False)
    makes = _cards(await work.compute_work_list(shop), "Make")
    assert len(makes) == 2  # black PETG is not black PLA


async def test_the_most_urgent_card_comes_first_and_lateness_shows(shop):
    pla = await _material(shop, "PLA", "filament")
    first = await _product(shop, name="Late one", sku="L-1", filament=pla)
    second = await _product(shop, name="Soon one", sku="S-1", filament=pla)
    await _order(shop, second, 1, due_in_days=3)
    await _order(shop, first, 1, due_in_days=-1)
    cards = _cards(await work.compute_work_list(shop), "Make")
    assert [c.product_name for c in cards] == ["Late one", "Soon one"]
    assert cards[0].late is True and cards[1].late is False


# ---- per-order stages -------------------------------------------------------------------------


async def test_a_personalised_file_gates_making_and_is_recorded_per_order(shop):
    pla = await _material(shop, "PLA", "filament")
    file_stage = await _stage(shop, "Make file", per_order=True)
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, name="Name plaque", sku="N-1", filament=pla)
    await _set_route(shop, product, "Personalised", StepInput(stage_id=file_stage.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id))
    order_a = await _order(shop, product, 1, due_in_days=2)
    order_b = await _order(shop, product, 2, due_in_days=3)

    listing = await work.compute_work_list(shop)
    files = _cards(listing, "Make file")
    assert len(files) == 2 and {c.order_ids[0] for c in files} == {order_a, order_b}  # one card per order, never merged
    assert _cards(listing, "Make") == []  # nothing makeable until a file exists

    from app.models.order import OrderLine

    line_a = (await shop.execute(select(OrderLine).where(OrderLine.order_id == order_a))).scalar_one()
    await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=file_stage.id, qty=1, order_line_id=line_a.id)
    listing = await work.compute_work_list(shop)
    assert [c.order_ids for c in _cards(listing, "Make file")] == [[order_b]]
    [make] = _cards(listing, "Make")
    assert make.qty == 1 and make.order_ids == [order_a]  # only the order whose file exists


async def test_a_per_order_stage_needs_the_order_and_a_pooled_one_refuses_it(shop):
    pla = await _material(shop, "PLA", "filament")
    file_stage, mask = await _stage(shop, "Make file", per_order=True), await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, filament=pla)
    await _set_route(
        shop, product, "R", StepInput(stage_id=file_stage.id), StepInput(stage_id=mask.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id)
    )
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=file_stage.id, qty=1)
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=1, order_line_id=1)


async def test_a_per_order_stage_must_come_before_make(shop):
    file_stage = await _stage(shop, "Late file", per_order=True)
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    with pytest.raises(WorkConfigError):
        await work_routes.create_route(
            shop, name="Bad", steps=[StepInput(stage_id=make.id), StepInput(stage_id=file_stage.id), StepInput(stage_id=ship.id)]
        )


# ---- finishing and whole-order shipping -------------------------------------------------------


async def test_finishing_gates_the_ship_card(shop):
    pla = await _material(shop, "PLA", "filament")
    glue = await _stage(shop, "Assemble")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, filament=pla)
    await _set_route(shop, product, "Glued", StepInput(stage_id=make.id), StepInput(stage_id=glue.id), StepInput(stage_id=ship.id))
    order_id = await _order(shop, product, 2)
    await _build(shop, product, 2)

    listing = await work.compute_work_list(shop)
    [assemble] = _cards(listing, "Assemble")
    assert assemble.qty == 2 and assemble.order_ids == [order_id]
    assert listing.ship_cards == [] and "still to finish" in listing.waiting_orders[0].waiting_for[0]

    await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=glue.id, qty=2)
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Assemble") == []
    assert [o.order_id for o in listing.ship_cards] == [order_id]


async def test_stock_that_predates_the_feature_needs_no_finishing(shop):
    pla = await _material(shop, "PLA", "filament")
    glue = await _stage(shop, "Assemble")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, stock=5, filament=pla)
    await _set_route(shop, product, "Glued", StepInput(stage_id=make.id), StepInput(stage_id=glue.id), StepInput(stage_id=ship.id))
    order_id = await _order(shop, product, 2)
    listing = await work.compute_work_list(shop)
    assert _cards(listing, "Assemble") == [] and [o.order_id for o in listing.ship_cards] == [order_id]


async def test_an_order_ships_whole(shop):
    """Two lines: one in stock, one still to make. The order is waiting, with 1 of 2 lines ready."""
    pla = await _material(shop, "PLA", "filament")
    in_stock = await _product(shop, name="In stock", sku="A", stock=3, filament=pla)
    to_make = await _product(shop, name="To make", sku="B", filament=pla)
    read = await create_order(
        OrderCreate(
            lines=[
                OrderLineInput(product_id=in_stock.id, ordered_qty=1, unit_price=Decimal("5")),
                OrderLineInput(product_id=to_make.id, ordered_qty=1, unit_price=Decimal("5")),
            ]
        ),
        session=shop,
    )
    listing = await work.compute_work_list(shop)
    assert listing.ship_cards == []
    [waiting] = listing.waiting_orders
    assert (waiting.ready_lines, waiting.total_lines) == (1, 2)
    assert waiting.waiting_for == ["To make: still to make"]

    await _build(shop, to_make, 1)
    listing = await work.compute_work_list(shop)
    assert [o.order_id for o in listing.ship_cards] == [read.id]


async def test_finished_stock_is_not_double_counted_across_orders(shop):
    """One finished unit unaccounted for by finishing can only release one of two single-unit orders."""
    pla = await _material(shop, "PLA", "filament")
    glue = await _stage(shop, "Assemble")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, filament=pla)
    await _set_route(shop, product, "Glued", StepInput(stage_id=make.id), StepInput(stage_id=glue.id), StepInput(stage_id=ship.id))
    first = await _order(shop, product, 1, due_in_days=1)
    second = await _order(shop, product, 1, due_in_days=2)
    await _build(shop, product, 2)
    await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=glue.id, qty=1)
    listing = await work.compute_work_list(shop)
    assert [o.order_id for o in listing.ship_cards] == [first]
    assert [o.order_id for o in listing.waiting_orders] == [second]


# ---- recording --------------------------------------------------------------------------------


async def test_make_and_ship_cannot_be_ticked(shop):
    product = await _product(shop)
    make = await _builtin(shop, WorkStageKind.make)
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=make.id, qty=1)


async def test_a_stage_outside_the_products_route_is_refused(shop):
    product = await _product(shop)
    other = await _stage(shop, "Unrelated")
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=other.id, qty=1)


async def test_cannot_take_back_more_than_was_recorded(shop):
    mask = await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop)
    await _set_route(shop, product, "R", StepInput(stage_id=mask.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id))
    await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=3)
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=-4)
    await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=-3)
    with pytest.raises(WorkConfigError):
        await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=0)


async def test_undo_writes_the_opposite_and_only_once(shop):
    mask = await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop)
    await _set_route(shop, product, "R", StepInput(stage_id=mask.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id))
    [event] = await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=4)
    reverse = await work.undo_event(shop, event.id)
    assert reverse.qty == -4
    with pytest.raises(WorkConfigError):
        await work.undo_event(shop, event.id)


async def test_one_tick_on_a_mixed_batch_is_spread_over_the_variants(shop):
    pla = await _material(shop, "PLA", "filament")
    mask = await _stage(shop, "Mask")
    make, ship = await _builtin(shop, WorkStageKind.make), await _builtin(shop, WorkStageKind.ship)
    product = await _product(shop, filament=pla)
    a, b = await _variant(shop, product, "A"), await _variant(shop, product, "B")
    await _set_route(shop, product, "R", StepInput(stage_id=mask.id, mix_variants=True), StepInput(stage_id=make.id), StepInput(stage_id=ship.id))
    await _order(shop, product, 2, variant=a, due_in_days=1)
    await _order(shop, product, 3, variant=b, due_in_days=2)
    [card] = _cards(await work.compute_work_list(shop), "Mask")
    assert card.qty == 5

    events = await work.record_progress(shop, product_id=product.id, variant_id=None, stage_id=mask.id, qty=4)
    by_variant = {e.variant_id: e.qty for e in events}
    assert by_variant == {a.id: 2, b.id: 2}  # most urgent variant filled first
    [card] = _cards(await work.compute_work_list(shop), "Mask")
    assert card.qty == 1 and card.parts[0].variant_name == "B"


async def test_summary_counts_late_today_and_ready(shop):
    pla = await _material(shop, "PLA", "filament")
    product = await _product(shop, filament=pla, stock=1)
    await _order(shop, product, 1, due_in_days=-1)  # ready to ship, late
    other = await _product(shop, name="Other", sku="O-1", filament=pla)
    await _order(shop, other, 1, due_in_days=5)
    summary = await work.compute_work_summary(shop)
    assert summary.enabled and summary.late == 1 and summary.ready_to_ship == 1 and summary.open_cards == 2


async def test_summary_when_off(session):
    await work_routes.ensure_work_defaults(session)
    summary = await work.compute_work_summary(session)
    assert summary.enabled is False and summary.open_cards == 0
