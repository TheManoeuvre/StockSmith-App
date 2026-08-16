"""The stock take lifecycle (services/stock_takes.py).

The assertion that matters most is test_a_short_count_on_an_allocated_line_is_flagged: a
product with 10 on hand and 5 allocated, counted as the 5 loose units, lands *exactly* on
the floor the adjustment service checks. Nothing refuses it, so without the allocation rule
it applies as a clean count and silently writes off five real units that are boxed by the
door. Everything else here is ordinary lifecycle cover; that one is guarding against
invisible data loss.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialAdjustment, MaterialCategory, MaterialUnit
from app.models.product import Product, ProductBundleItem
from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus
from app.models.variant import ProductVariant
from app.schemas.stock_take import BulkLineCount, StockTakeScope
from app.services import stock_takes
from app.services.abc import compute_due_for_count

MATERIALS_ONLY = StockTakeScope(include_materials=True)
PRODUCTS_ONLY = StockTakeScope(include_products=True)
EVERYTHING = StockTakeScope(include_materials=True, include_products=True)


async def _settings(session) -> None:
    session.add(GeneralSettings(id=1))
    await session.flush()


async def _material(
    session,
    name="Grey Resin",
    qty=Decimal(0),
    category=MaterialCategory.resin,
    unit=MaterialUnit.ml,
    **kwargs,
) -> Material:
    m = Material(name=name, category=category, unit=unit, current_qty=qty, **kwargs)
    session.add(m)
    await session.flush()
    # current_qty is derived from the adjustment ledger, so seed history to match rather
    # than relying on the column — recompute_material would otherwise reset it to zero.
    if qty:
        session.add(MaterialAdjustment(material_id=m.id, mode="adjust", qty_delta=qty, reason="opening"))
    await session.flush()
    return m


async def _product(session, name="Oak Coaster", stock=0, allocated=0, **kwargs) -> Product:
    p = Product(name=name, sku=f"SKU-{name}", current_stock=stock, allocated_qty=allocated, **kwargs)
    session.add(p)
    await session.flush()
    return p


async def _lines(session, take_id) -> list[StockTakeLine]:
    return list(
        (
            await session.execute(
                select(StockTakeLine).where(StockTakeLine.stock_take_id == take_id).order_by(StockTakeLine.id)
            )
        ).scalars()
    )


async def _count(session, take_id, line_id, qty):
    await stock_takes.set_line_counts(session, take_id, [BulkLineCount(line_id=line_id, counted_qty=qty)])


# --- scoping and start -----------------------------------------------------------------


async def test_start_snapshots_the_current_quantities(session):
    await _settings(session)
    await _material(session, qty=Decimal(7))
    await session.commit()

    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)

    lines = await _lines(session, take.id)
    assert [Decimal(line.expected_qty) for line in lines] == [Decimal(7)]
    assert lines[0].status is StockTakeLineStatus.pending
    assert lines[0].counted_qty is None


async def test_a_product_with_variants_contributes_its_variants(session):
    await _settings(session)
    product = await _product(session)
    session.add_all(
        [
            ProductVariant(product_id=product.id, variant_name="Red", current_stock=3),
            ProductVariant(product_id=product.id, variant_name="Blue", current_stock=4),
        ]
    )
    await session.commit()

    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)

    lines = await _lines(session, take.id)
    assert {line.variant_id is not None for line in lines} == {True}
    assert sorted(Decimal(line.expected_qty) for line in lines) == [Decimal(3), Decimal(4)]


async def test_bundles_are_never_counted(session):
    await _settings(session)
    component = await _product(session, "Component", stock=2)
    bundle = await _product(session, "Gift Set", is_bundle=True)
    session.add(ProductBundleItem(bundle_product_id=bundle.id, component_product_id=component.id, qty=1))
    await session.commit()

    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)

    assert [line.product_id for line in await _lines(session, take.id)] == [component.id]


async def test_allocated_qty_is_snapshotted_for_products_only(session):
    await _settings(session)
    await _material(session, qty=Decimal(5))
    await _product(session, stock=10, allocated=4)
    await session.commit()

    take, _ = await stock_takes.create_stock_take(session, EVERYTHING)

    lines = await _lines(session, take.id)
    material_line = next(line for line in lines if line.material_id)
    product_line = next(line for line in lines if line.product_id)
    assert material_line.allocated_qty_at_start is None
    assert Decimal(product_line.allocated_qty_at_start) == Decimal(4)


async def test_scoping_by_category_and_overdue(session):
    await _settings(session)
    await _material(session, "Resin")
    await _material(session, "Boxes", category=MaterialCategory.packaging)
    await session.commit()

    by_category = await stock_takes.preview_scope(
        session, StockTakeScope(include_materials=True, material_categories=[MaterialCategory.packaging])
    )
    assert by_category.candidate_count == 1

    # Everything is due on a database with no counting history, so overdue_only is a no-op
    # here — the point is that it doesn't accidentally exclude everything.
    overdue = await stock_takes.preview_scope(
        session, StockTakeScope(include_materials=True, overdue_only=True)
    )
    assert overdue.candidate_count == 2


async def test_an_empty_scope_is_refused_rather_than_creating_a_take_with_no_lines(session):
    await _settings(session)
    await session.commit()

    with pytest.raises(HTTPException):
        await stock_takes.create_stock_take(session, MATERIALS_ONLY)


async def test_overlapping_takes_warn_and_proceed(session):
    """A soft lock: reported, never enforced."""
    await _settings(session)
    await _material(session, qty=Decimal(5))
    await session.commit()
    first, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)

    second, warnings = await stock_takes.create_stock_take(session, MATERIALS_ONLY)

    assert second.id != first.id
    assert len(await _lines(session, second.id)) == 1
    assert [w.other_stock_take_id for w in warnings] == [first.id]


# --- approve ---------------------------------------------------------------------------


async def test_a_plain_variance_is_applied(session, pushes):
    await _settings(session)
    material = await _material(session, qty=Decimal(10))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(8))

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.conflicts, counts.skipped) == (1, 0, 0)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(8)
    await session.refresh(line)
    assert line.status is StockTakeLineStatus.applied
    assert line.material_adjustment_id is not None
    assert material.last_stock_take_id == take.id


async def test_a_blank_line_is_skipped_and_not_re_dated(session, pushes):
    """The rule most likely to regress: leaving a line blank asserts nothing about it."""
    await _settings(session)
    material = await _material(session, qty=Decimal(10))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.skipped) == (0, 1)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(10)
    assert material.last_stock_take_at is None
    assert (await _lines(session, take.id))[0].status is StockTakeLineStatus.skipped


async def test_a_confirming_count_still_dates_the_item(session, pushes):
    """Zero delta, but it was counted — that is the whole reason a zero-delta set is legal."""
    await _settings(session)
    material = await _material(session, qty=Decimal(10))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(10))

    await stock_takes.approve_stock_take(session, take.id)

    await session.refresh(material)
    assert material.last_stock_take_at is not None
    assert await compute_due_for_count(session) == []


async def test_movement_since_the_snapshot_is_flagged_not_applied(session, pushes):
    await _settings(session)
    material = await _material(session, qty=Decimal(10))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(8))
    # Something consumes stock mid-count.
    session.add(MaterialAdjustment(material_id=material.id, mode="adjust", qty_delta=Decimal(-3), reason="build"))
    from app.services.costing import recompute_material

    await recompute_material(session, material.id)
    await session.commit()

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.conflicts) == (0, 1)
    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(7)  # untouched by the take
    await session.refresh(line)
    assert line.status is StockTakeLineStatus.conflict
    assert "moved since" in line.conflict_reason
    assert material.last_stock_take_at is None


async def test_a_short_count_on_an_allocated_line_is_flagged(session, pushes):
    """The silent write-off.

    10 on hand, 5 allocated and boxed by the door. Counting the 5 loose units gives a total
    of 5, which is exactly the allocated floor — create_stock_adjustment accepts it, and
    without this rule the take reports success while destroying five real units.
    """
    await _settings(session)
    product = await _product(session, stock=10, allocated=5)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(5))

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.conflicts) == (0, 1)
    await session.refresh(product)
    assert product.current_stock == 10, "boxed-but-unshipped stock was written off"
    await session.refresh(line)
    assert line.status is StockTakeLineStatus.conflict
    assert "allocated to open orders" in line.conflict_reason


async def test_a_short_count_with_no_allocation_still_applies(session, pushes):
    """The rule must not quietly turn every variance into manual review."""
    await _settings(session)
    product = await _product(session, stock=10, allocated=0)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(6))

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.conflicts) == (1, 0)
    await session.refresh(product)
    assert product.current_stock == 6


async def test_a_count_above_expected_on_an_allocated_line_applies(session, pushes):
    """Finding more than expected raises no question about picked stock."""
    await _settings(session)
    product = await _product(session, stock=10, allocated=5)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(12))

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert counts.applied == 1
    await session.refresh(product)
    assert product.current_stock == 12


async def test_one_flagged_line_does_not_stop_the_others(session, pushes):
    """Mixed take: each line is decided independently and the take still closes."""
    await _settings(session)
    await _material(session, "Resin", qty=Decimal(10))
    flagged = await _product(session, "Boxed", stock=10, allocated=5)
    clean = await _product(session, "Clean", stock=4)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, EVERYTHING)
    lines = {line.id: line for line in await _lines(session, take.id)}
    for line_id, line in lines.items():
        if line.material_id:
            await _count(session, take.id, line_id, Decimal(9))
        elif line.product_id == flagged.id:
            await _count(session, take.id, line_id, Decimal(5))
        else:
            await _count(session, take.id, line_id, Decimal(3))

    counts = await stock_takes.approve_stock_take(session, take.id)

    assert (counts.applied, counts.conflicts) == (2, 1)
    await session.refresh(clean)
    await session.refresh(flagged)
    assert clean.current_stock == 3
    assert flagged.current_stock == 10


async def test_approving_closes_the_take_even_with_conflicts_outstanding(session, pushes):
    await _settings(session)
    await _product(session, stock=10, allocated=5)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(5))

    await stock_takes.approve_stock_take(session, take.id)

    await session.refresh(take)
    assert take.status is StockTakeStatus.closed
    assert take.closed_at is not None
    assert len(await stock_takes.unresolved_variances(session)) == 1


async def test_approve_is_idempotent(session, pushes):
    await _settings(session)
    product = await _product(session, stock=10)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(7))
    await stock_takes.approve_stock_take(session, take.id)

    second = await stock_takes.approve_stock_take(session, take.id)

    assert (second.applied, second.conflicts, second.skipped) == (0, 0, 0)
    await session.refresh(product)
    assert product.current_stock == 7


# --- resolving -------------------------------------------------------------------------


async def _flagged_take(session):
    product = await _product(session, stock=10, allocated=5)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(5))
    await stock_takes.approve_stock_take(session, take.id)
    return take, (await _lines(session, take.id))[0], product


async def test_accept_system_discards_the_count(session, pushes):
    await _settings(session)
    take, line, product = await _flagged_take(session)

    await stock_takes.resolve_line(session, take.id, line.id, "accept_system")

    await session.refresh(line)
    await session.refresh(product)
    assert line.status is StockTakeLineStatus.accepted_system
    assert product.current_stock == 10
    assert await stock_takes.unresolved_variances(session) == []


async def test_accept_counted_applies_it_after_all(session, pushes):
    await _settings(session)
    take, line, product = await _flagged_take(session)

    await stock_takes.resolve_line(session, take.id, line.id, "accept_counted")

    await session.refresh(line)
    await session.refresh(product)
    assert line.status is StockTakeLineStatus.applied
    assert product.current_stock == 5
    assert await stock_takes.unresolved_variances(session) == []


async def test_reset_on_a_closed_take_becomes_skipped(session, pushes):
    """No sheet to re-enter it in, so it means "leave it, I'll count it next time" — the
    item keeps its old date and stays due."""
    await _settings(session)
    take, line, product = await _flagged_take(session)

    await stock_takes.resolve_line(session, take.id, line.id, "reset")

    await session.refresh(line)
    await session.refresh(product)
    assert line.status is StockTakeLineStatus.skipped
    assert line.counted_qty is None
    assert product.current_stock == 10
    assert product.last_stock_take_at is None


async def test_reset_on_an_open_take_returns_the_line_to_pending(session, pushes):
    await _settings(session)
    await _product(session, stock=10)
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, PRODUCTS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(7))

    await stock_takes.resolve_line(session, take.id, line.id, "reset")

    await session.refresh(line)
    assert line.status is StockTakeLineStatus.pending
    assert line.counted_qty is None


# --- counting and housekeeping ---------------------------------------------------------


async def test_counts_are_validated_against_the_unit(session):
    """Reuses validate_qty_for_unit, so an `each` material refuses a fraction here for the
    same reason it does anywhere else."""
    await _settings(session)
    await _material(session, "Screws", category=MaterialCategory.hardware, unit=MaterialUnit.each, qty=Decimal(10))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]

    with pytest.raises(HTTPException):
        await _count(session, take.id, line.id, Decimal("2.5"))


async def test_clearing_a_count_returns_the_line_to_pending(session):
    await _settings(session)
    await _material(session, qty=Decimal(5))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]
    await _count(session, take.id, line.id, Decimal(4))
    assert (await _lines(session, take.id))[0].status is StockTakeLineStatus.counted

    await _count(session, take.id, line.id, None)

    refreshed = (await _lines(session, take.id))[0]
    assert refreshed.status is StockTakeLineStatus.pending
    assert refreshed.counted_qty is None


async def test_counting_on_a_closed_take_is_refused(session, pushes):
    await _settings(session)
    await _material(session, qty=Decimal(5))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    line = (await _lines(session, take.id))[0]
    await stock_takes.approve_stock_take(session, take.id)

    with pytest.raises(HTTPException):
        await _count(session, take.id, line.id, Decimal(4))


async def test_an_open_take_can_be_abandoned_but_a_closed_one_cannot(session, pushes):
    await _settings(session)
    await _material(session, qty=Decimal(5))
    await session.commit()
    take, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)

    await stock_takes.delete_stock_take(session, take.id)
    assert await session.get(StockTake, take.id) is None

    second, _ = await stock_takes.create_stock_take(session, MATERIALS_ONLY)
    await stock_takes.approve_stock_take(session, second.id)
    with pytest.raises(HTTPException):
        await stock_takes.delete_stock_take(session, second.id)
