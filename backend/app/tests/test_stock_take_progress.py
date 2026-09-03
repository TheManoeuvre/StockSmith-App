"""The list/detail progress fields the router derives from a take's lines.

`counted_count` is a snapshot of the *live* `counted` state, so it drops back to zero the
moment a take is approved and its counted lines move on to `applied` / `conflict` /
`skipped`. `completed_count` and `progress_status` are what the list actually shows, and
they have to keep describing a closed take truthfully — "how far did this one get", not
"how many lines are mid-count right now".
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialAdjustment, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.product import Product
from app.models.stock_take import StockTake, StockTakeLine
from app.routers.stock_takes import _progress_status, _take_fields
from app.schemas.stock_take import BulkLineCount, StockTakeProgress, StockTakeScope
from app.services import stock_takes
from app.services.material_categories import legacy_value_for

MATERIALS_ONLY = StockTakeScope(include_materials=True)
PRODUCTS_ONLY = StockTakeScope(include_products=True)


async def _settings(session) -> None:
    session.add(GeneralSettings(id=1))
    await session.flush()


async def _material(session, name="Grey Resin", qty=Decimal(0), category="resin") -> Material:
    row = (
        await session.execute(select(MaterialCategory).where(MaterialCategory.name == category))
    ).scalar_one()
    m = Material(
        name=name,
        category=legacy_value_for(category),
        category_id=row.id,
        unit=MaterialUnit.ml,
        current_qty=qty,
    )
    session.add(m)
    await session.flush()
    if qty:
        session.add(MaterialAdjustment(material_id=m.id, mode="adjust", qty_delta=qty, reason="opening"))
    await session.flush()
    return m


async def _product(session, name="Oak Coaster", stock=0, allocated=0) -> Product:
    p = Product(name=name, sku=f"SKU-{name}", current_stock=stock, allocated_qty=allocated)
    session.add(p)
    await session.flush()
    return p


async def _lines(session, take_id):
    return list(
        (
            await session.execute(
                select(StockTakeLine)
                .where(StockTakeLine.stock_take_id == take_id)
                .order_by(StockTakeLine.id)
            )
        ).scalars()
    )


async def _count(session, take_id, line_id, qty) -> None:
    await stock_takes.set_line_counts(session, take_id, [BulkLineCount(line_id=line_id, counted_qty=qty)])


async def _reread(session_factory, take_id) -> tuple[StockTake, list[StockTakeLine], dict]:
    """The take, its lines, and the derived fields — read from a session that has seen the
    approve writes, exactly as the list endpoint does."""
    async with session_factory() as s:
        take = await s.get(StockTake, take_id)
        lines = await _lines(s, take_id)
        return take, lines, _take_fields(take, lines)


async def test_open_take_is_open_and_counts_the_counted_lines(session):
    await _settings(session)
    await _material(session, "A", qty=Decimal(3))
    await _material(session, "B", qty=Decimal(5))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    lines = await _lines(session, take.id)
    await _count(session, take.id, lines[0].id, Decimal(3))

    lines = await _lines(session, take.id)
    fields = _take_fields(take, lines)
    assert _progress_status(take, lines) is StockTakeProgress.open
    assert fields["progress_status"] is StockTakeProgress.open
    assert (fields["completed_count"], fields["line_count"]) == (1, 2)


async def test_every_line_applied_reads_completed(session, session_factory, pushes):
    await _settings(session)
    await _product(session, "One", stock=10)
    await _product(session, "Two", stock=4)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    for line in await _lines(session, take.id):
        await _count(session, take.id, line.id, Decimal(2))
    await stock_takes.approve_stock_take(session, take.id)

    take, _lines_, fields = await _reread(session_factory, take.id)
    assert fields["progress_status"] is StockTakeProgress.completed
    # The bug this guards: counted_count is 0 now, but progress must still read 2 / 2.
    assert fields["counted_count"] == 0
    assert (fields["completed_count"], fields["line_count"]) == (2, 2)


async def test_some_applied_some_blank_reads_partially_completed(session, session_factory, pushes):
    await _settings(session)
    await _product(session, "Counted", stock=10)
    await _product(session, "Blank", stock=4)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    counted = next(line for line in await _lines(session, take.id) if line.product_id)
    await _count(session, take.id, counted.id, Decimal(9))
    await stock_takes.approve_stock_take(session, take.id)

    _take, _lines_, fields = await _reread(session_factory, take.id)
    assert fields["progress_status"] is StockTakeProgress.partially_completed
    assert (fields["completed_count"], fields["line_count"]) == (1, 2)


async def test_a_flagged_count_still_counts_toward_completed(session, session_factory, pushes):
    """A line counted but held for review had a count entered — it belongs in the tally,
    and with nothing applied the take is still just Closed."""
    await _settings(session)
    await _product(session, stock=10, allocated=5)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(5))  # loose units -> conflict
    await stock_takes.approve_stock_take(session, take.id)

    _take, _lines_, fields = await _reread(session_factory, take.id)
    assert fields["progress_status"] is StockTakeProgress.closed
    assert fields["completed_count"] == 1


async def test_closed_with_nothing_counted_reads_closed(session, session_factory, pushes):
    await _settings(session)
    await _product(session, stock=10)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    await stock_takes.approve_stock_take(session, take.id)

    _take, _lines_, fields = await _reread(session_factory, take.id)
    assert fields["progress_status"] is StockTakeProgress.closed
    assert fields["completed_count"] == 0
