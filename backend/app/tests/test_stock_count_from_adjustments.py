"""A manual "Set" adjustment counts as a physical count (Phase A.1).

The app already had a way to record a count — Set mode on the adjust forms, which the
adjustment models document as exactly that — it just had no effect on the counting
schedule, so recounting an item by hand left it still showing as due. These cover the
rule that fixes it, and the line either side of it: "adjust" is a known delta and must
NOT date the item, because it says what changed rather than that the total was verified.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialAdjustmentMode, MaterialCategory, MaterialUnit
from app.models.product import Product
from app.models.stock_adjustment import StockAdjustmentMode
from app.models.variant import ProductVariant
from app.services.abc import compute_due_for_count
from app.services.costing import create_adjustment
from app.services.stock_adjustments import create_stock_adjustment


async def _settings(session) -> None:
    session.add(GeneralSettings(id=1))
    await session.flush()


async def _material(session, name="Grey Resin", **kwargs) -> Material:
    m = Material(name=name, category=MaterialCategory.resin, unit=MaterialUnit.ml, **kwargs)
    session.add(m)
    await session.flush()
    return m


async def _product(session, name="Oak Coaster", **kwargs) -> Product:
    p = Product(name=name, sku=f"SKU-{name}", **kwargs)
    session.add(p)
    await session.flush()
    return p


# --- materials -------------------------------------------------------------------------


async def test_set_adjustment_dates_the_material(session):
    await _settings(session)
    material = await _material(session)
    await session.commit()
    assert material.last_stock_take_at is None

    await create_adjustment(session, material.id, MaterialAdjustmentMode.set, Decimal(53), "Recount")

    await session.refresh(material)
    assert material.last_stock_take_at is not None


async def test_adjust_adjustment_does_not_date_the_material(session):
    """Breakage tells you what left, not that what remains was ever counted.

    Stocked via an `adjust` rather than by setting current_qty, which is derived — writing
    it directly is ignored by recompute_material and the material stays empty.
    """
    await _settings(session)
    material = await _material(session)
    await session.commit()
    await create_adjustment(session, material.id, MaterialAdjustmentMode.adjust, Decimal(10), "Stock in")

    await create_adjustment(session, material.id, MaterialAdjustmentMode.adjust, Decimal(-2), "Breakage")

    await session.refresh(material)
    assert Decimal(material.current_qty) == Decimal(8)
    assert material.last_stock_take_at is None


async def test_a_set_confirming_the_current_qty_still_counts(session):
    """Zero delta, but the count happened — the whole point is not having to count it
    again next week. The models already allow a zero-delta 'set' for this reason."""
    await _settings(session)
    material = await _material(session)
    await session.commit()

    await create_adjustment(session, material.id, MaterialAdjustmentMode.set, Decimal(0), "Recount, still empty")

    await session.refresh(material)
    assert material.last_stock_take_at is not None


async def test_a_refused_material_adjustment_raises_before_dating_anything(session):
    """A refused adjustment counted nothing, so it must not restart the clock.

    Only the raise is asserted here, not the stored date, and that is a limitation rather
    than a choice: this session cannot be read from afterwards. `create_adjustment` rolls
    back internally on refusal, and in this dependency set (SQLAlchemy 2.0.51 / aiosqlite
    0.22.1) that leaves the session permanently unusable — even a bare column SELECT then
    raises MissingGreenlet, and an explicit rollback by the caller does not recover it.
    See docs/backlog.md; it is also why Phase B's approve loop cannot simply catch a
    refusal and carry on with the same session.

    The invariant itself is structural: the date is assigned after both negative-qty
    guards and inside the same transaction, so a refusal cannot leave it moved. The
    products-side equivalent below asserts the stored value directly, because that guard
    runs before any database write and so leaves the session intact.
    """
    await _settings(session)
    material = await _material(session)
    await session.commit()
    await create_adjustment(session, material.id, MaterialAdjustmentMode.adjust, Decimal(5), "Stock in")

    with pytest.raises(HTTPException):
        await create_adjustment(session, material.id, MaterialAdjustmentMode.set, Decimal(-50), "Impossible")


async def test_a_set_adjustment_clears_the_material_off_the_due_list(session):
    """The behaviour actually asked for: recount it by hand, stop being told to count it."""
    await _settings(session)
    material = await _material(session)
    await session.commit()
    assert [d.name for d in await compute_due_for_count(session)] == ["Grey Resin"]

    await create_adjustment(session, material.id, MaterialAdjustmentMode.set, Decimal(53), "Recount")

    assert await compute_due_for_count(session) == []


# --- products and variants -------------------------------------------------------------


async def test_set_adjustment_dates_the_product(session, pushes):
    await _settings(session)
    product = await _product(session)
    await session.commit()

    await create_stock_adjustment(session, product.id, None, StockAdjustmentMode.set, 12, "Recount")

    await session.refresh(product)
    assert product.last_stock_take_at is not None


async def test_set_adjustment_dates_the_variant_not_its_product(session, pushes):
    """A variant holds its own stock and is counted in its own right, so the date belongs
    on the row that was actually counted. Dating the product instead would mark every
    other variant as counted too."""
    await _settings(session)
    product = await _product(session)
    counted = ProductVariant(product_id=product.id, variant_name="Red")
    untouched = ProductVariant(product_id=product.id, variant_name="Blue")
    session.add_all([counted, untouched])
    await session.commit()

    await create_stock_adjustment(session, product.id, counted.id, StockAdjustmentMode.set, 4, "Recount")

    await session.refresh(counted)
    await session.refresh(untouched)
    await session.refresh(product)
    assert counted.last_stock_take_at is not None
    assert untouched.last_stock_take_at is None
    assert product.last_stock_take_at is None
    assert [d.name for d in await compute_due_for_count(session)] == ["Oak Coaster — Blue"]


async def test_adjust_adjustment_does_not_date_the_product(session, pushes):
    await _settings(session)
    product = await _product(session, current_stock=10)
    await session.commit()

    await create_stock_adjustment(session, product.id, None, StockAdjustmentMode.adjust, -2, "Damaged")

    await session.refresh(product)
    assert product.last_stock_take_at is None


async def test_an_adjustment_refused_by_the_allocation_floor_leaves_the_date_alone(session, pushes):
    """The floor case that Phase B routes to manual review — here it just must not date."""
    await _settings(session)
    product = await _product(session, current_stock=10, allocated_qty=5)
    await session.commit()

    with pytest.raises(HTTPException):
        await create_stock_adjustment(session, product.id, None, StockAdjustmentMode.set, 3, "Recount")

    assert product.last_stock_take_at is None


async def test_the_date_is_recent_enough_to_satisfy_the_cadence(session):
    """Guards against writing a naive datetime that due_state then reads as long past —
    SQLite doesn't round-trip tzinfo, so this is a real way to get it wrong."""
    await _settings(session)
    material = await _material(session, stock_take_interval_days=1)
    await session.commit()

    await create_adjustment(session, material.id, MaterialAdjustmentMode.set, Decimal(1), "Recount")

    await session.refresh(material)
    assert await compute_due_for_count(session) == []
    # And it becomes due again once the cadence has elapsed.
    later = datetime.now(timezone.utc) + timedelta(days=2)
    assert [d.name for d in await compute_due_for_count(session, now=later)] == ["Grey Resin"]
