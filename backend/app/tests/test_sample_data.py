"""The sample workspace's generator runs end to end against a fresh database, through the app's
own functions, and leaves the app in the state the Dashboard is meant to show off."""

from sqlalchemy import func, select

from app.models.general_settings import GeneralSettings
from app.models.material import Material
from app.models.order import Order
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.variant import ProductVariant
from app.seed import ensure_seed_data
from app.services.buildability import compute_dashboard_summary
from app.services.sample_data import build_sample


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def test_builds_a_believable_candle_shop(session, pushes):
    await ensure_seed_data(session)
    await build_sample(session)

    settings = await session.get(GeneralSettings, 1)
    assert settings.business_type == "candles_soap"
    assert settings.setup_checklist_dismissed is True

    assert await _count(session, Material) == 13
    assert await _count(session, Product) == 3
    assert await _count(session, ProductVariant) == 4
    assert await _count(session, Order) == 23
    assert await _count(session, Order, Order.shipped_at.is_not(None)) >= 15
    assert await _count(session, Purchase, Purchase.received_at.is_(None)) == 1

    # Stock and cost came from a received purchase, so wax has both.
    wax = (await session.scalars(select(Material).where(Material.name == "Soy container wax"))).one()
    assert wax.current_qty > 0 and wax.avg_unit_cost > 0

    # The newest order wants more large sea-salt candles than exist: something to build.
    summary = await compute_dashboard_summary(session)
    assert summary.orders_awaiting_inventory


async def test_the_sample_has_a_storage_layout_that_followed_its_builds_and_orders(session, pushes):
    from decimal import Decimal

    from app.models.location import Location, PlacementEventKind, PlacementEvent
    from app.services import placements
    from app.services.placement_hooks import ItemRef

    await ensure_seed_data(session)
    await build_sample(session)

    names = set((await session.scalars(select(Location.name))).all())
    assert {"Workshop", "Stockroom", "Loft", "Wax shelf", "Bin 1", "Bin 3", "Shelf A", "Shelf B"} <= names

    # No placement ever claims more than exists, and the demo has no drift to explain.
    sums = await placements.summaries(session)
    assert all(s.drift == 0 for s in sums.values())

    # Builds and shipped orders took stock off the shelf by themselves.
    assert await _count(session, PlacementEvent, PlacementEvent.kind == PlacementEventKind.auto_deduct) > 0
    wax = (await session.scalars(select(Material).where(Material.name == "Soy container wax"))).one()
    wax_view = await placements.stored_in(session, ItemRef(material_id=wax.id))
    assert wax_view.follows_stock and wax_view.placed == Decimal(wax.current_qty)

    # The states the Locations page should have something to show for.
    jars = (await session.scalars(select(Material).where(Material.name == "Amber jar 30cl"))).one()
    jar_view = await placements.stored_in(session, ItemRef(material_id=jars.id))
    assert len(jar_view.placements) == 2 and jar_view.unassigned > 0
    labels = (await session.scalars(select(Material).where(Material.name == "Product label"))).one()
    assert (await placements.stored_in(session, ItemRef(material_id=labels.id))).placements == []


async def test_the_sample_shows_work_flow_with_a_route_in_the_shops_own_words(session, pushes):
    from app.services import work, work_routes

    await ensure_seed_data(session)
    await build_sample(session)

    assert (await work_routes.get_work_settings(session)).work_flow_enabled is True
    routes = {r.name: r for r in await work_routes.list_routes(session)}
    assert [s.stage.name for s in routes["Container candle"].steps] == ["Prepare jar", "Make", "Cure", "Ship"]

    listing = await work.compute_work_list(session)
    # The pending order for 3 large sea-salt candles has 2 on the shelf, so one is short: it starts with preparing a jar; pouring comes
    # once they're ready, because the list shows what can be done now.
    [prepare] = [c for c in listing.cards if c.stage_name == "Prepare jar"]
    assert prepare.qty == 1 and "Make" not in {c.stage_name for c in listing.cards}
    # The stock built before work flow was switched on is not waiting to cure.
    assert "Cure" not in {c.stage_name for c in listing.cards}
    # The one open order is the short one, so it is waiting rather than ready to pack.
    assert not listing.ship_cards and len(listing.waiting_orders) == 1

    await work.record_progress(
        session, product_id=prepare.product_id, variant_id=prepare.parts[0].variant_id, stage_id=prepare.stage_id, qty=1
    )
    listing = await work.compute_work_list(session)
    assert [c.stage_name for c in listing.cards] == ["Make"]
