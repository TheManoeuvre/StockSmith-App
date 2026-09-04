"""Materials Weeks-of-Supply forecast (services/forecasting.py).

Covers: the worked-example baseline and its multi-product aggregation, per-material
scrap sourced from BuildFailedConsumption (not a blanket yield ratio), a kitting-only
material forecast (no scrap term, no FG-buffer delay), on-order arrival timing, and the
insufficient-history fallback to the static reorder_threshold.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.build import Build, BuildFailedConsumption
from app.models.general_settings import GeneralSettings
from app.models.kitting import ProductKittingMaterial
from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product, ProductMaterial
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.models.supplier import Supplier
from app.services.forecasting import compute_material_forecasts

NOW = datetime.now(timezone.utc)


async def _material(session, name="Filament", category=LegacyMaterialCategory.filament, **kwargs) -> Material:
    m = Material(name=name, category=category, unit=MaterialUnit.g, **kwargs)
    session.add(m)
    await session.flush()
    return m


async def _product(session, name, sku, current_stock=0, allocated_qty=0) -> Product:
    p = Product(name=name, sku=sku, current_stock=current_stock, allocated_qty=allocated_qty)
    session.add(p)
    await session.flush()
    return p


async def _place_order(session, product_id: int, qty: int, weeks_ago: float) -> None:
    order = Order(order_placed_at=NOW - timedelta(weeks=weeks_ago), status=OrderStatus.allocated)
    order.lines = [OrderLine(product_id=product_id, ordered_qty=qty, allocated_qty=0)]
    session.add(order)
    await session.flush()


async def _settings(session, **overrides) -> GeneralSettings:
    settings = GeneralSettings(id=1, **overrides)
    session.add(settings)
    await session.commit()
    return settings


async def test_worked_example_baseline(session):
    """2/week, 2 finished units on hand, materials for 4 more -> 3 weeks of cover."""
    await _settings(session)
    material = await _material(session, current_qty=Decimal(4))
    product = await _product(session, "Blue Bottle Opener", "SKU-1", current_stock=2)
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product.id, qty=2, weeks_ago=w)
    await session.commit()

    forecasts = await compute_material_forecasts(session)
    assert len(forecasts) == 1
    f = forecasts[0]
    assert f.material_id == material.id
    assert f.weeks_of_supply == Decimal(3)
    assert f.fg_buffer_weeks == Decimal(1)
    assert f.consumption_rate_per_week == Decimal(2)


async def test_shared_material_shortens_coverage(session):
    """A second product drawing on the same material (with no finished-goods buffer of
    its own) shortens the material's coverage — demand must aggregate across every
    product that consumes it, not just the one the user happens to be looking at."""
    await _settings(session)
    material = await _material(session, current_qty=Decimal(4))
    product_a = await _product(session, "Blue Bottle Opener", "SKU-1", current_stock=2)
    product_b = await _product(session, "Other Blue Item", "SKU-2", current_stock=0)
    session.add(ProductMaterial(product_id=product_a.id, material_id=material.id, qty_required=Decimal(1)))
    session.add(ProductMaterial(product_id=product_b.id, material_id=material.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product_a.id, qty=2, weeks_ago=w)
        await _place_order(session, product_b.id, qty=1, weeks_ago=w)
    await session.commit()

    forecasts = await compute_material_forecasts(session)
    assert len(forecasts) == 1
    assert forecasts[0].weeks_of_supply == Decimal(2)
    assert forecasts[0].consumption_rate_per_week == Decimal(3)


async def test_scrap_is_per_material_not_a_blanket_yield_ratio(session):
    """A failed build that only consumed filament (not hardware) must raise filament's
    forecasted demand without touching hardware's — a single product-wide yield ratio
    applied to every BOM line would incorrectly inflate hardware's demand too.

    Builds directly against the Build/BuildFailedConsumption models rather than through
    services.builds.create_build — that service also replays Material history from
    scratch via recompute_material, which would wipe out a current_qty set directly on
    the model without a matching purchase/adjustment trail. This test only cares about
    the demand-rate calculation, which reads Build/BuildFailedConsumption directly."""
    await _settings(session)
    filament = await _material(session, name="Filament", current_qty=Decimal(10))
    hardware = await _material(session, name="Hardware", category=LegacyMaterialCategory.hardware, current_qty=Decimal(10))
    product = await _product(session, "Widget", "SKU-3", current_stock=0)
    session.add(ProductMaterial(product_id=product.id, material_id=filament.id, qty_required=Decimal(1)))
    session.add(ProductMaterial(product_id=product.id, material_id=hardware.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product.id, qty=2, weeks_ago=w)
    await session.commit()

    # 50% failure rate, filament consumed on every failure, hardware never consumed —
    # the "filament checked by default, everything else not" build-failure default.
    build = Build(product_id=product.id, variant_id=None, qty_built=8, qty_failed=8)
    session.add(build)
    await session.flush()
    session.add(
        BuildFailedConsumption(build_id=build.id, material_id=filament.id, was_consumed=True, qty_consumed=Decimal(8))
    )
    session.add(
        BuildFailedConsumption(build_id=build.id, material_id=hardware.id, was_consumed=False, qty_consumed=Decimal(0))
    )
    await session.commit()

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    assert forecasts[filament.id].consumption_rate_per_week == Decimal(4)  # 2/wk x (1 + 1*1)
    assert forecasts[hardware.id].consumption_rate_per_week == Decimal(2)  # unaffected by the failures


async def test_kitting_material_has_no_scrap_term_or_fg_buffer(session):
    """A packaging material (label/bag) draws straight off the sales rate — no yield
    adjustment (a shipment doesn't "fail" the way a print does) and no finished-goods
    buffer delay (packaging is consumed at ship time)."""
    await _settings(session)
    label = await _material(session, name="Label", category=LegacyMaterialCategory.packaging, current_qty=Decimal(10))
    product = await _product(session, "Widget", "SKU-4", current_stock=5)  # FG stock present but irrelevant here
    session.add(ProductKittingMaterial(product_id=product.id, material_id=label.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product.id, qty=2, weeks_ago=w)
    await session.commit()

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    f = forecasts[label.id]
    assert f.consumption_rate_per_week == Decimal(2)
    assert f.fg_buffer_weeks == Decimal(0)
    assert f.weeks_of_supply == Decimal(5)  # 10 on hand / 2 per week, undelayed


async def test_on_order_timing_only_credits_arrivals_before_stockout(session):
    """A PO arriving after the material would otherwise run dry doesn't get credited;
    one arriving before that point correctly extends coverage."""
    await _settings(session)
    material = await _material(session, current_qty=Decimal(0))
    product = await _product(session, "Widget", "SKU-5", current_stock=0)
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product.id, qty=1, weeks_ago=w)
    await session.commit()

    today = date.today()
    near_purchase = Purchase(status=PurchaseStatus.ordered, expected_arrival_date=today)
    near_purchase.lines = [MaterialPurchase(material_id=material.id, qty=Decimal(2), total_cost=Decimal(0))]
    far_purchase = Purchase(status=PurchaseStatus.ordered, expected_arrival_date=today + timedelta(days=70))
    far_purchase.lines = [MaterialPurchase(material_id=material.id, qty=Decimal(100), total_cost=Decimal(0))]
    session.add_all([near_purchase, far_purchase])
    await session.commit()

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    # Runs out at week 2 (2 units on order arriving now, drawn down at 1/wk) — long before
    # the far PO's 100 units arrive at week 10, so that PO must not mask the stockout.
    assert forecasts[material.id].weeks_of_supply == Decimal(2)

    # Move the far PO's arrival to before the stockout point — coverage should now extend
    # well past the warning threshold, since it's a timed inflow just like the near one
    # (the material drops off the alert list entirely once it's no longer imminent).
    far_purchase.expected_arrival_date = today + timedelta(days=7)
    await session.commit()

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    assert material.id not in forecasts


async def test_insufficient_history_falls_back_to_reorder_threshold(session):
    """A material with too little sales history to forecast falls back to the static
    current_qty <= reorder_threshold check, flagged distinctly as insufficient_data."""
    await _settings(session)
    material = await _material(session, current_qty=Decimal(1), reorder_threshold=Decimal(5))
    product = await _product(session, "Widget", "SKU-6", current_stock=0)
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal(1)))
    await session.commit()

    # Only one week of sales activity — below the 2-distinct-week minimum.
    await _place_order(session, product.id, qty=2, weeks_ago=0)
    await session.commit()

    forecasts = await compute_material_forecasts(session)
    assert len(forecasts) == 1
    assert forecasts[0].status == "insufficient_data"
    assert forecasts[0].weeks_of_supply is None


async def test_material_above_threshold_with_no_history_is_not_surfaced(session):
    await _settings(session)
    await _material(session, current_qty=Decimal(10), reorder_threshold=Decimal(5))
    await session.commit()

    forecasts = await compute_material_forecasts(session)
    assert forecasts == []


async def test_include_all_returns_every_material_with_a_status(session):
    """The materials list/detail want a row per material, not just the alerting ones."""
    await _settings(session)
    healthy = await _material(session, name="Healthy", current_qty=Decimal(1000))
    no_history = await _material(session, name="No history", current_qty=Decimal(10), reorder_threshold=Decimal(5))
    product = await _product(session, "Blue Bottle Opener", "SKU-1", current_stock=0)
    session.add(ProductMaterial(product_id=product.id, material_id=healthy.id, qty_required=Decimal(1)))
    await session.commit()

    for w in range(8):
        await _place_order(session, product.id, qty=2, weeks_ago=w)
    await session.commit()

    # Default: the healthy material with plenty of cover is not surfaced at all.
    assert await compute_material_forecasts(session) == []

    by_id = {f.material_id: f for f in await compute_material_forecasts(session, include_all=True)}
    assert set(by_id) == {healthy.id, no_history.id}
    assert by_id[healthy.id].status == "ok"
    assert by_id[healthy.id].weeks_of_supply is not None
    assert by_id[no_history.id].status == "insufficient_data"
    assert by_id[no_history.id].weeks_of_supply is None


async def _eight_weeks_of_cover(session, **material_kwargs):
    """A material with exactly 8 weeks of undelayed cover: 8 on hand, drawn at 1/week."""
    material = await _material(session, current_qty=Decimal(8), **material_kwargs)
    product = await _product(session, "Widget", f"SKU-{material.id}", current_stock=0)
    session.add(ProductMaterial(product_id=product.id, material_id=material.id, qty_required=Decimal(1)))
    await session.commit()
    for w in range(8):
        await _place_order(session, product.id, qty=1, weeks_ago=w)
    await session.commit()
    return material


async def test_lead_time_pushes_the_reorder_point_out(session):
    """8 weeks of cover clears a 6-week warning threshold with no lead time — but a
    supplier that takes 15 business days (3 weeks) to deliver makes those same 8 weeks a
    warning, because the reorder point is judged lead-time-ahead of stockout."""
    await _settings(session, forecast_warning_weeks=Decimal(6), default_lead_time_days=0)
    supplier = Supplier(name="Slow Co", default_lead_time_days=15)
    session.add(supplier)
    await session.flush()
    material = await _eight_weeks_of_cover(session, default_supplier_id=supplier.id)

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    f = forecasts[material.id]
    assert f.weeks_of_supply == Decimal(8)
    assert f.status == "warning"
    assert f.lead_time_days == 15


async def test_shop_wide_default_lead_time_applies_without_a_supplier(session):
    """No supplier on the material — the shop-wide default lead time still widens the net."""
    await _settings(session, forecast_warning_weeks=Decimal(6), default_lead_time_days=15)
    material = await _eight_weeks_of_cover(session)

    forecasts = {f.material_id: f for f in await compute_material_forecasts(session)}
    assert forecasts[material.id].status == "warning"
    assert forecasts[material.id].lead_time_days == 15


async def test_no_lead_time_leaves_healthy_cover_unflagged(session):
    """The widening is strictly opt-in: with the shop default at 0 and no supplier figure,
    8 weeks of cover against a 6-week threshold is still healthy and not surfaced."""
    await _settings(session, forecast_warning_weeks=Decimal(6), default_lead_time_days=0)
    await _eight_weeks_of_cover(session)

    assert await compute_material_forecasts(session) == []
