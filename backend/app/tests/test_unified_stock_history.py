"""Item 4: unified "Stock" history — every build (success or failed), stock adjustment,
and order fulfillment writes one ProductStockEvent row with a running balance, and a
failed build lets the caller pick which BOM lines were actually consumed (filament
defaulted to consumed when not specified).
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.material import Material, MaterialAdjustment, MaterialCategory, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.models.product_stock_event import ProductStockEvent, ProductStockEventType
from app.services.builds import create_build
from app.services.costing import recompute_material
from app.services.stock_adjustments import create_stock_adjustment
from app.models.stock_adjustment import StockAdjustmentMode


async def _product_with_bom(session, filament_qty: Decimal = Decimal("1"), hardware_qty: Decimal = Decimal("1")) -> tuple[Product, Material, Material]:
    filament = Material(name="PLA Black", category=MaterialCategory.filament, unit=MaterialUnit.g)
    hardware = Material(name="M3 Insert", category=MaterialCategory.hardware, unit=MaterialUnit.each)
    session.add_all([filament, hardware])
    await session.flush()
    # current_qty is a derived/replayed column (see costing.recompute_material) — stock
    # has to arrive via a real adjustment row, not by setting the column directly.
    session.add(MaterialAdjustment(material_id=filament.id, qty_delta=Decimal("1000"), reason="seed"))
    session.add(MaterialAdjustment(material_id=hardware.id, qty_delta=Decimal("1000"), reason="seed"))
    await recompute_material(session, filament.id)
    await recompute_material(session, hardware.id)
    product = Product(name="Widget", sku="SKU-1", current_stock=0, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add_all(
        [
            ProductMaterial(product_id=product.id, material_id=filament.id, qty_required=filament_qty),
            ProductMaterial(product_id=product.id, material_id=hardware.id, qty_required=hardware_qty),
        ]
    )
    await session.commit()
    return product, filament, hardware


async def _events_for(session, product_id: int) -> list[ProductStockEvent]:
    return list(
        (
            await session.execute(
                select(ProductStockEvent)
                .where(ProductStockEvent.product_id == product_id)
                .order_by(ProductStockEvent.id)
            )
        ).scalars()
    )


async def test_successful_build_creates_stock_event_with_running_balance(session):
    product, _, _ = await _product_with_bom(session)

    build = await create_build(session, product.id, None, qty_built=5, notes="batch 1")

    events = await _events_for(session, product.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == ProductStockEventType.build_success
    assert event.qty_delta == 5
    assert event.running_balance == 5
    assert event.source_build_id == build.id


async def test_failed_build_defaults_to_consuming_filament_only(session):
    product, filament, hardware = await _product_with_bom(session, filament_qty=Decimal("2"), hardware_qty=Decimal("1"))

    build = await create_build(session, product.id, None, qty_built=0, notes=None, qty_failed=3)

    events = await _events_for(session, product.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == ProductStockEventType.build_failed
    assert event.qty_delta == 0
    assert event.running_balance == 0  # no stock produced

    await session.refresh(filament)
    await session.refresh(hardware)
    # 3 failed * 2 qty_required consumed for filament; hardware untouched (not checked by default)
    assert Decimal(filament.current_qty) == Decimal("1000") - Decimal("6")
    assert Decimal(hardware.current_qty) == Decimal("1000")

    from app.models.build import BuildFailedConsumption

    consumption = {
        row.material_id: row
        for row in (
            await session.execute(select(BuildFailedConsumption).where(BuildFailedConsumption.build_id == build.id))
        ).scalars()
    }
    assert consumption[filament.id].was_consumed is True
    assert Decimal(consumption[filament.id].qty_consumed) == Decimal("6")
    assert consumption[hardware.id].was_consumed is False
    assert Decimal(consumption[hardware.id].qty_consumed) == Decimal("0")


async def test_failed_build_honors_explicit_consumption_override(session):
    product, filament, hardware = await _product_with_bom(session)

    await create_build(
        session,
        product.id,
        None,
        qty_built=0,
        notes=None,
        qty_failed=2,
        failed_consumption={filament.id: False, hardware.id: True},
    )

    await session.refresh(filament)
    await session.refresh(hardware)
    assert Decimal(filament.current_qty) == Decimal("1000")  # not consumed, overridden off
    assert Decimal(hardware.current_qty) == Decimal("1000") - Decimal("2")


async def test_build_with_both_success_and_failure_creates_two_events(session):
    product, _, _ = await _product_with_bom(session)

    build = await create_build(session, product.id, None, qty_built=4, notes=None, qty_failed=1)

    events = await _events_for(session, product.id)
    assert len(events) == 2
    by_type = {e.event_type: e for e in events}
    assert by_type[ProductStockEventType.build_success].qty_delta == 4
    assert by_type[ProductStockEventType.build_success].running_balance == 4
    assert by_type[ProductStockEventType.build_failed].qty_delta == 0
    assert by_type[ProductStockEventType.build_failed].running_balance == 4
    assert by_type[ProductStockEventType.build_success].source_build_id == build.id
    assert by_type[ProductStockEventType.build_failed].source_build_id == build.id


async def test_stock_adjustment_creates_stock_event(session):
    product, _, _ = await _product_with_bom(session)
    product.current_stock = 10
    await session.commit()

    adjustment = await create_stock_adjustment(session, product.id, None, StockAdjustmentMode.adjust, -3, "breakage")

    events = await _events_for(session, product.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == ProductStockEventType.adjustment
    assert event.qty_delta == -3
    assert event.running_balance == 7
    assert event.source_adjustment_id == adjustment.id
    assert event.reason == "breakage"


async def test_list_stock_history_endpoint_joins_source_detail(session):
    from app.routers.products import list_stock_history

    product, _, _ = await _product_with_bom(session)
    await create_build(session, product.id, None, qty_built=3, notes="run 1")
    await create_stock_adjustment(session, product.id, None, StockAdjustmentMode.set, 10, "recount")

    reads = await list_stock_history(product.id, session=session)

    assert len(reads) == 2  # newest first
    adjustment_read, build_read = reads
    assert adjustment_read.event_type == ProductStockEventType.adjustment
    assert adjustment_read.adjustment_mode == StockAdjustmentMode.set
    assert adjustment_read.adjustment_target_qty == 10
    assert build_read.event_type == ProductStockEventType.build_success
    assert build_read.build_qty_built == 3


async def test_ship_line_creates_order_fulfillment_stock_event(session):
    from app.models.order import Order, OrderLine
    from app.services import allocation

    product, _, _ = await _product_with_bom(session)
    product.current_stock = 10
    product.allocated_qty = 5
    session.add(Order(currency="GBP"))
    await session.flush()
    order = (await session.execute(select(Order))).scalar_one()
    line = OrderLine(order_id=order.id, product_id=product.id, ordered_qty=5, allocated_qty=5)
    session.add(line)
    await session.commit()

    await allocation.ship_line(session, line, 2)
    await session.commit()

    events = await _events_for(session, product.id)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == ProductStockEventType.order_fulfillment
    assert event.qty_delta == -2
    assert event.running_balance == 8
    assert event.source_order_line_id == line.id
