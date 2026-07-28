"""End-to-end policy tests for the paid-only import gate, driven through
`order_sync.commit_sync` against a fake adapter.

These assert on stock counters and allocation events rather than just on row counts,
because "no Order row" is only half the requirement — the half users would actually have
noticed is that an unpaid order must not reserve stock or take quantity off sale on the
live marketplace listing.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.models.allocation_event import AllocationEvent
from app.models.listing import ListingPlatform
from app.models.order import Order, OrderLine, OrderStatus
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode
from app.models.product import Product
from app.services import allocation, order_sync
from app.services.platforms.base import PaymentState

from .conftest import make_order


async def _stocked_product(session, sku: str = "SKU-1", qty: int = 10) -> Product:
    product = Product(name="Test product", sku=sku, current_stock=qty, allocated_qty=0)
    session.add(product)
    await session.commit()
    return product


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def _reload(session, product: Product) -> Product:
    await session.refresh(product)
    return product


# --- The core requirement ----------------------------------------------------------


async def test_unsettled_new_order_is_not_imported_and_reserves_nothing(
    session, session_factory, connection, use_adapter, pushes
):
    product = await _stocked_product(session)
    use_adapter([make_order("R-UNPAID", payment_state=PaymentState.unsettled, sku="SKU-1")])

    result = await order_sync.commit_sync(ListingPlatform.etsy)

    assert result.created_count == 0
    assert result.skipped_unpaid_count == 1
    assert await _count(session, Order) == 0
    assert await _count(session, OrderLine) == 0
    assert await _count(session, AllocationEvent) == 0
    # The part a seller would have seen: stock stays free, and nothing is pushed to the
    # live listing.
    assert (await _reload(session, product)).allocated_qty == 0
    assert pushes == []


async def test_settled_new_order_is_imported_and_allocated(session, session_factory, connection, use_adapter, pushes):
    """The false-negative guard: a gate that rejects everything would also pass the test
    above, so this has to fail loudly if the gate is too aggressive."""
    product = await _stocked_product(session)
    use_adapter([make_order("R-PAID", payment_state=PaymentState.settled, sku="SKU-1")])

    result = await order_sync.commit_sync(ListingPlatform.etsy)

    assert result.created_count == 1
    assert result.skipped_unpaid_count == 0
    assert await _count(session, Order) == 1
    assert (await _reload(session, product)).allocated_qty == 1
    assert pushes  # quantity change did propagate outward


async def test_order_imports_exactly_once_when_it_later_settles(
    session, session_factory, connection, use_adapter, pushes
):
    """The lifecycle that matters: unpaid on the first poll, paid on the next. It must
    appear once, and be allocated once — not twice, and not never."""
    product = await _stocked_product(session)
    t1 = datetime.now(timezone.utc) - timedelta(hours=2)

    use_adapter([make_order("R-LATER", payment_state=PaymentState.unsettled, sku="SKU-1", last_modified=t1)])
    await order_sync.commit_sync(ListingPlatform.etsy)
    assert await _count(session, Order) == 0

    use_adapter(
        [make_order("R-LATER", payment_state=PaymentState.settled, sku="SKU-1", last_modified=t1 + timedelta(hours=1))]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)

    assert await _count(session, Order) == 1
    assert await _count(session, AllocationEvent) == 1
    assert (await _reload(session, product)).allocated_qty == 1


# --- Reversed payments -------------------------------------------------------------


async def test_new_reversed_order_is_imported_but_never_allocated(
    session, session_factory, connection, use_adapter, pushes
):
    """A first sighting that is already refunded keeps its history but must not reserve."""
    product = await _stocked_product(session)
    use_adapter([make_order("R-REFUND", payment_state=PaymentState.reversed, sku="SKU-1")])

    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    assert order.pending_marketplace_cancellation is True
    assert "reversed" in order.sync_issue
    assert await _count(session, AllocationEvent) == 0
    assert (await _reload(session, product)).allocated_qty == 0
    assert pushes == []


async def test_reversal_after_import_flags_but_does_not_deallocate(
    session, session_factory, connection, use_adapter, pushes
):
    """A chargeback on an order already holding stock is a human decision (scrap vs
    return-to-stock), so the reservation must survive until they make it."""
    product = await _stocked_product(session)
    use_adapter([make_order("R-CB", payment_state=PaymentState.settled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)
    assert (await _reload(session, product)).allocated_qty == 1

    use_adapter(
        [
            make_order(
                "R-CB",
                payment_state=PaymentState.reversed,
                sku="SKU-1",
                last_modified=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        ]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    assert order.pending_marketplace_cancellation is True
    assert order.status is not OrderStatus.cancelled
    assert (await _reload(session, product)).allocated_qty == 1  # still reserved


async def test_flag_clears_when_the_marketplace_stops_reporting_a_problem(
    session, session_factory, connection, use_adapter, pushes
):
    """Both flag-setting branches fail closed, so a partial response or an unrecognised
    payment status can flag a healthy order. That must be recoverable without a human —
    otherwise a transient hiccup would permanently stop the order receiving stock, since
    auto_allocate_after_build now skips flagged orders."""
    await _stocked_product(session)
    use_adapter([make_order("R-BLIP", payment_state=PaymentState.settled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)

    use_adapter([make_order("R-BLIP", payment_state=PaymentState.unsettled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    assert order.pending_marketplace_cancellation is True

    use_adapter([make_order("R-BLIP", payment_state=PaymentState.settled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(order)

    assert order.pending_marketplace_cancellation is False
    assert order.sync_issue is None


async def test_already_imported_order_is_never_dropped_by_the_gate(
    session, session_factory, connection, use_adapter, pushes
):
    """Reconciliation must keep running for known orders whatever their payment says —
    otherwise a later shipment or cancellation would never be picked up."""
    await _stocked_product(session)
    use_adapter([make_order("R-KNOWN", payment_state=PaymentState.settled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)

    use_adapter([make_order("R-KNOWN", payment_state=PaymentState.unsettled, sku="SKU-1")])
    result = await order_sync.commit_sync(ListingPlatform.etsy)

    assert result.skipped_unpaid_count == 0
    assert result.updated_count == 1
    assert await _count(session, Order) == 1


# --- Watermark and the unpaid hold -------------------------------------------------


async def test_hold_keeps_the_window_open_behind_the_watermark(session, session_factory, connection, use_adapter):
    """Skipping an unpaid order still advances the watermark past it, so without a hold a
    platform that doesn't bump last_modified on settlement would lose it forever."""
    t_old = datetime.now(timezone.utc) - timedelta(days=2)
    t_new = datetime.now(timezone.utc) - timedelta(hours=1)
    use_adapter(
        [
            make_order("R-UNPAID", payment_state=PaymentState.unsettled, last_modified=t_old),
            make_order("R-PAID", payment_state=PaymentState.settled, last_modified=t_new),
        ]
    )

    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(connection)

    # Watermark still advances normally to the newest thing seen...
    assert connection.last_orders_synced_at.replace(tzinfo=timezone.utc) == t_new + timedelta(seconds=1)
    # ...but the effective fetch window is dragged back behind the held order.
    assert connection.unpaid_hold_since is not None
    assert order_sync._effective_since(connection) < t_old


async def test_hold_clears_once_the_order_settles(session, session_factory, connection, use_adapter):
    t = datetime.now(timezone.utc) - timedelta(hours=3)
    use_adapter([make_order("R-X", payment_state=PaymentState.unsettled, last_modified=t)])
    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(connection)
    assert connection.unpaid_hold_since is not None

    use_adapter([make_order("R-X", payment_state=PaymentState.settled, last_modified=t + timedelta(minutes=1))])
    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(connection)

    assert connection.unpaid_hold_since is None


async def test_hold_ignores_orders_older_than_the_maximum(session, session_factory, connection, use_adapter):
    """An order left unpaid forever must not pin the fetch window open forever."""
    ancient = datetime.now(timezone.utc) - (order_sync._MAX_UNPAID_HOLD + timedelta(days=1))
    use_adapter([make_order("R-ANCIENT", payment_state=PaymentState.unsettled, last_modified=ancient)])

    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(connection)

    assert connection.unpaid_hold_since is None


async def test_sync_start_date_floor_still_beats_the_hold(session, session_factory, connection, use_adapter):
    floor_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    connection.sync_start_date = floor_date
    connection.unpaid_hold_since = datetime.now(timezone.utc) - timedelta(days=10)
    await session.commit()

    since = order_sync._effective_since(connection)

    assert since.date() == floor_date


# --- Observability and the financials regression -----------------------------------


async def test_skipped_count_is_recorded_on_the_run(session, session_factory, connection, use_adapter):
    use_adapter(
        [
            make_order("A", payment_state=PaymentState.unsettled),
            make_order("B", payment_state=PaymentState.unsettled),
            make_order("C", payment_state=PaymentState.settled),
        ]
    )

    await order_sync.commit_sync(ListingPlatform.etsy)

    run = (
        await session.execute(select(PlatformSyncRun).where(PlatformSyncRun.mode == SyncRunMode.commit))
    ).scalar_one()
    assert run.skipped_unpaid_count == 2
    assert run.new_count == 1


async def test_build_does_not_allocate_to_a_flagged_order(session, session_factory, connection, use_adapter, pushes):
    """The escape hatch the gate would otherwise leave open.

    auto_allocate_after_build selects purely on Order.status, and a flagged order still
    reads as `pending` — so before the guard, building stock for a product on a
    payment-reversed order would hand that order the stock anyway, from a completely
    different code path than sync.
    """
    product = await _stocked_product(session, qty=0)
    use_adapter([make_order("R-FLAG", payment_state=PaymentState.reversed, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)

    # Stock arrives afterwards, exactly as a build would deliver it.
    product.current_stock = 5
    await session.commit()
    await allocation.auto_allocate_after_build(session, product.id, None, source="build#1")
    await session.commit()

    line = (await session.execute(select(OrderLine))).scalar_one()
    assert line.allocated_qty == 0
    assert (await _reload(session, product)).allocated_qty == 0


async def test_build_still_allocates_to_a_healthy_order(session, session_factory, connection, use_adapter, pushes):
    """Positive control for the guard above — it must not have broken ordinary FIFO
    top-up of a normal order that simply couldn't be filled at import time."""
    product = await _stocked_product(session, qty=0)
    use_adapter([make_order("R-OK", payment_state=PaymentState.settled, sku="SKU-1")])
    await order_sync.commit_sync(ListingPlatform.etsy)

    product.current_stock = 5
    await session.commit()
    await allocation.auto_allocate_after_build(session, product.id, None, source="build#1")
    await session.commit()

    line = (await session.execute(select(OrderLine))).scalar_one()
    assert line.allocated_qty == 1


async def test_unenriched_refetch_does_not_wipe_stored_financials(
    session, session_factory, connection, use_adapter, pushes
):
    """The hold re-fetches old orders with enrichment deliberately skipped. Writing those
    absent payment fields anyway would silently destroy a breakdown an earlier sync got
    right."""
    await _stocked_product(session)
    use_adapter(
        [
            make_order(
                "R-FIN",
                payment_state=PaymentState.settled,
                sku="SKU-1",
                payment_fees="1.50",
                payment_net="8.50",
                payment_status="SETTLED",
            )
        ]
    )
    await order_sync.commit_sync(ListingPlatform.etsy)

    use_adapter([make_order("R-FIN", payment_state=PaymentState.settled, sku="SKU-1", financials_enriched=False)])
    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    assert order.payment_fees == Decimal("1.50")
    assert order.payment_net == Decimal("8.50")
    assert order.payment_status == "SETTLED"
