"""Foreign-key enforcement, and the order-deletion cascade that depends on it.

`DELETE /orders/{id}` deletes the order row and leaves four child tables —
`allocation_events`, `order_line_returns`, `order_kitting_allocations` and
`order_kitting_overrides` — to database-level `ON DELETE CASCADE`, because none of them
has an ORM relationship to hang `cascade="all, delete-orphan"` off the way `Order.lines`
does. SQLite ships with `PRAGMA foreign_keys = 0`, so before `enforce_sqlite_foreign_keys`
existed that cascade never fired and every order deleted through the UI leaked its audit
rows (six such orphans were found in the live database on 2026-07-28 — see
docs/cleanup-2026-07-28-unpaid-orders.md).

The engine fixtures are local to this module rather than in conftest.py deliberately: the
whole point here is comparing an engine that has the pragma against one that does not, so
"which engine am I using" has to be visible in each test.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.db import enforce_sqlite_foreign_keys
from app.models.allocation_event import AllocationEvent, AllocationEventType
from app.models.base import Base
from app.models.kitting import OrderKittingAllocation, OrderKittingOverride
from app.models.material import Material, MaterialAdjustment, LegacyMaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine
from app.models.order_return import OrderLineReturn, ReturnDisposition, ReturnScope, ReturnSource
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant


async def _make_engine(*, enforce_fks: bool):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_fks:
        enforce_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


@pytest_asyncio.fixture
async def enforced_engine():
    engine = await _make_engine(enforce_fks=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def unenforced_engine():
    """A plain engine, exactly as app/db.py built it before this change — the baseline the
    orphan-producing bug was observed on."""
    engine = await _make_engine(enforce_fks=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(enforced_engine):
    async with async_sessionmaker(enforced_engine, expire_on_commit=False)() as s:
        yield s


@pytest_asyncio.fixture
async def unenforced_session(unenforced_engine):
    async with async_sessionmaker(unenforced_engine, expire_on_commit=False)() as s:
        yield s


async def _seed_order_with_every_child(session) -> tuple[Order, OrderLine]:
    """An order carrying one row in each table that hangs off it, direct or via its line.

    Quantities mirror what `delete_order` actually permits: it refuses to delete an order
    with allocated or shipped units, so a deletable order's ledger rows are the residue of
    an allocate-then-deallocate cycle. That is precisely the shape of the live orphans.
    """
    product = Product(name="Test Product", sku="SKU-TEST-1", current_stock=5)
    material = Material(
        name="Test Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, current_qty=Decimal(10)
    )
    session.add_all([product, material])
    await session.flush()

    order = Order(buyer_name="Test Buyer")
    session.add(order)
    await session.flush()

    line = OrderLine(order_id=order.id, product_id=product.id, ordered_qty=2, allocated_qty=0, shipped_qty=0)
    session.add(line)
    await session.flush()

    session.add_all(
        [
            # Children of order_lines.
            AllocationEvent(
                order_line_id=line.id,
                product_id=product.id,
                event_type=AllocationEventType.allocate,
                qty=2,
                source="order-create",
            ),
            AllocationEvent(
                order_line_id=line.id,
                product_id=product.id,
                event_type=AllocationEventType.deallocate,
                qty=2,
                source="manual",
            ),
            OrderLineReturn(
                order_line_id=line.id,
                scope=ReturnScope.product,
                qty=Decimal(2),
                disposition=ReturnDisposition.return_to_stock,
                source=ReturnSource.cancel_before_ship,
            ),
            # Children of orders.
            OrderKittingAllocation(
                order_id=order.id, material_id=material.id, reserved_qty=Decimal(0), consumed_qty=Decimal(0)
            ),
            OrderKittingOverride(order_id=order.id, material_id=material.id, qty_required=Decimal(1)),
        ]
    )
    await session.commit()
    return order, line


async def _child_counts(session, order_id: int, line_id: int) -> dict[str, int]:
    async def count(model, column, value):
        return (await session.execute(select(func.count()).select_from(model).where(column == value))).scalar_one()

    return {
        "order_lines": await count(OrderLine, OrderLine.order_id, order_id),
        "allocation_events": await count(AllocationEvent, AllocationEvent.order_line_id, line_id),
        "order_line_returns": await count(OrderLineReturn, OrderLineReturn.order_line_id, line_id),
        "order_kitting_allocations": await count(
            OrderKittingAllocation, OrderKittingAllocation.order_id, order_id
        ),
        "order_kitting_overrides": await count(OrderKittingOverride, OrderKittingOverride.order_id, order_id),
    }


async def test_pragma_is_on_for_every_connection_from_an_enforced_engine(enforced_engine):
    """The pragma is per-connection and resets when the pool recycles one, so this asserts
    the listener fires on connect rather than that some one-off statement ran at startup."""
    for _ in range(2):
        async with enforced_engine.connect() as conn:
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1


async def test_pragma_is_off_without_the_helper(unenforced_engine):
    """Guards the premise of the test below: SQLite's default really is enforcement off."""
    async with unenforced_engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 0


async def test_deleting_an_order_removes_every_child_row(session):
    order, line = await _seed_order_with_every_child(session)
    before = await _child_counts(session, order.id, line.id)
    assert before == {
        "order_lines": 1,
        "allocation_events": 2,
        "order_line_returns": 1,
        "order_kitting_allocations": 1,
        "order_kitting_overrides": 1,
    }

    await session.delete(order)
    await session.commit()

    after = await _child_counts(session, order.id, line.id)
    assert after == dict.fromkeys(before, 0), f"child rows survived the delete: {after}"


async def test_deleting_an_order_orphans_child_rows_without_the_pragma(unenforced_session):
    """The bug itself, pinned. `Order.lines` has an ORM cascade so order_lines still goes,
    but the four tables relying purely on database-level CASCADE are left behind — which is
    exactly the six-row orphan set found in the live database."""
    order, line = await _seed_order_with_every_child(unenforced_session)

    await unenforced_session.delete(order)
    await unenforced_session.commit()

    after = await _child_counts(unenforced_session, order.id, line.id)
    assert after == {
        "order_lines": 0,  # the ORM relationship cascade, which works either way
        "allocation_events": 2,
        "order_line_returns": 1,
        "order_kitting_allocations": 1,
        "order_kitting_overrides": 1,
    }


async def test_foreign_key_check_is_clean_after_deleting_an_order(session):
    """`PRAGMA foreign_key_check` is the diagnostic the live orphans were found with, so
    assert against it directly and not only against the per-table counts above."""
    order, _ = await _seed_order_with_every_child(session)
    await session.delete(order)
    await session.commit()

    violations = (await session.execute(text("PRAGMA foreign_key_check"))).fetchall()
    assert violations == []


async def test_deleting_an_order_keeps_material_adjustments_and_nulls_their_order_id(session):
    """`material_adjustments.order_id` is ON DELETE SET NULL, not CASCADE — packaging
    consumed for an order is a permanent stock movement and must survive the order being
    deleted. Enforcement turns the previously-dangling order_id into a real NULL, which is
    what the material stock-history view already renders (it tests `order_id != null`
    before linking)."""
    order, line = await _seed_order_with_every_child(session)
    material_id = (
        await session.execute(select(OrderKittingAllocation.material_id).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    session.add(
        MaterialAdjustment(
            material_id=material_id, qty_delta=Decimal(-1), reason="Kitting consumed", order_id=order.id
        )
    )
    await session.commit()

    await session.delete(order)
    await session.commit()

    rows = (await session.execute(select(MaterialAdjustment))).scalars().all()
    assert len(rows) == 1, "a stock movement must not be deleted with the order that caused it"
    assert rows[0].order_id is None
    assert rows[0].qty_delta == Decimal(-1)


async def test_deleting_a_shipping_profile_nulls_every_reference(session):
    """The other behaviour change enforcement brings: `delete_shipping_profile` is a hard
    delete with no ORM relationship pointing back at it, so before this the orders,
    products and variants using the profile kept a dangling id. All three columns are
    nullable and only ever read through a relationship or an explicit lookup, so SET NULL
    firing for real is a repair, not a regression — a shipped order's cost is unaffected
    because it lives in shipping_cost_snapshot."""
    profile = ShippingProfile(name="Test Profile", price=Decimal("3.50"), cost_manual=Decimal("3.00"))
    session.add(profile)
    await session.flush()

    product = Product(name="Profiled Product", sku="SKU-TEST-2", shipping_profile_id=profile.id)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id, variant_name="Default", sku_suffix="D", shipping_profile_id=profile.id
    )
    order = Order(buyer_name="Test Buyer", shipping_profile_id=profile.id, shipping_cost_snapshot=Decimal("3.50"))
    session.add_all([variant, order])
    await session.commit()

    await session.delete(profile)
    await session.commit()

    for obj in (product, variant, order):
        await session.refresh(obj)
        assert obj.shipping_profile_id is None
    assert order.shipping_cost_snapshot == Decimal("3.50")
    assert (await session.execute(text("PRAGMA foreign_key_check"))).fetchall() == []


async def test_hard_deleting_a_referenced_product_is_now_refused(session):
    """`order_lines.product_id` is ON DELETE RESTRICT, and enforcement makes that real.

    Nothing in the app hits this: `DELETE /products/{id}`, `/variants/{id}` and
    `/materials/{id}` are all soft deletes setting `is_active = False`, which is what keeps
    the schema's fifteen RESTRICT constraints unexercised. This test exists so that
    invariant is stated somewhere — turning any of those three into a real delete would
    start raising IntegrityError on historical orders, not silently shred them."""
    _, line = await _seed_order_with_every_child(session)
    product = await session.get(Product, line.product_id)

    await session.delete(product)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
