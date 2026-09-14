"""Order line substitution — always a split (see app.models.order_substitution), never a
mutation of the original line or a delete. Covers: whole-line and partial (split)
substitution, undo before/after shipping, chained substitution + undo, and the
same-product-only validation."""

from decimal import Decimal

from sqlalchemy import select

from app.models.order import Order, OrderLine
from app.models.product import Product
from app.models.variant import ProductVariant
from app.routers.orders import _get_order_with_lines, create_order, substitute_line, undo_substitution
from app.schemas.order import OrderCreate, OrderLineInput, SubstituteLineRequest
from app.services import allocation
from app.services.buildability import get_orders_awaiting_inventory


async def _product_with_variants(session, green_stock: int, blue_stock: int) -> tuple[Product, ProductVariant, ProductVariant]:
    product = Product(name="Tote Bag", variant_attribute1_name="Color")
    session.add(product)
    await session.flush()
    green = ProductVariant(product_id=product.id, variant_name="Green", attribute1_value="Green", current_stock=green_stock)
    blue = ProductVariant(product_id=product.id, variant_name="Blue", attribute1_value="Blue", current_stock=blue_stock)
    session.add_all([green, blue])
    await session.commit()
    return product, green, blue


async def _order_lines(session, order_id: int) -> list[OrderLine]:
    return list((await session.execute(select(OrderLine).where(OrderLine.order_id == order_id))).scalars())


async def test_whole_line_substitution_zeroes_original_and_allocates_new_line(session):
    product, green, blue = await _product_with_variants(session, green_stock=0, blue_stock=5)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=3, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]
    assert original.allocated_qty == 0  # nothing in stock for green

    result = await substitute_line(
        original.id, SubstituteLineRequest(variant_id=blue.id, qty=3, reason="customer changed mind"), session=session
    )

    lines = {l.variant_id: l for l in result.lines}
    assert lines[green.id].ordered_qty == 0
    assert lines[blue.id].ordered_qty == 3
    assert lines[blue.id].allocated_qty == 3  # blue had stock, so it's fully allocated

    green_line = lines[green.id]
    assert len(green_line.substituted_to) == 1
    assert green_line.substituted_to[0].qty == 3
    assert green_line.substituted_to[0].variant_name == "Blue"
    assert lines[blue.id].substituted_from is not None
    assert lines[blue.id].substituted_from.variant_name == "Green"


async def test_split_substitution_leaves_original_remainder_allocatable(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=10)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=5, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]
    assert original.allocated_qty == 5

    result = await substitute_line(
        original.id, SubstituteLineRequest(variant_id=blue.id, qty=2, reason=None), session=session
    )

    lines = {l.variant_id: l for l in result.lines}
    assert lines[green.id].ordered_qty == 3
    assert lines[green.id].allocated_qty == 3
    assert lines[blue.id].ordered_qty == 2
    assert lines[blue.id].allocated_qty == 2


async def test_undersupplied_substitute_line_shows_up_in_awaiting_inventory(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=0)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=3, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    await substitute_line(original.id, SubstituteLineRequest(variant_id=blue.id, qty=3, reason=None), session=session)

    awaiting = await get_orders_awaiting_inventory(session)
    assert any(row.variant_id == blue.id and row.short_by == 3 for row in awaiting)
    assert not any(row.variant_id == green.id for row in awaiting)


async def test_undo_restores_original_line_and_reallocates_it(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=10)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=5, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    substituted = await substitute_line(
        original.id, SubstituteLineRequest(variant_id=blue.id, qty=2, reason=None), session=session
    )
    blue_line = next(l for l in substituted.lines if l.variant_id == blue.id)
    substitution_id = blue_line.substituted_from.substitution_id

    result = await undo_substitution(substitution_id, session=session)

    lines = {l.variant_id: l for l in result.lines}
    assert lines[green.id].ordered_qty == 5
    assert lines[green.id].allocated_qty == 5
    assert lines[blue.id].ordered_qty == 0
    assert lines[blue.id].allocated_qty == 0
    assert lines[green.id].substituted_to == []
    assert lines[blue.id].substituted_from is None


async def test_undo_blocked_once_substitute_line_has_shipped(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=10)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=5, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    substituted = await substitute_line(
        original.id, SubstituteLineRequest(variant_id=blue.id, qty=2, reason=None), session=session
    )
    blue_line = next(l for l in substituted.lines if l.variant_id == blue.id)
    substitution_id = blue_line.substituted_from.substitution_id

    order = await _get_order_with_lines(session, order_read.id)
    new_line = next(l for l in await _order_lines(session, order.id) if l.variant_id == blue.id)
    await allocation.ship_line(session, new_line, 1)
    await session.commit()

    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        await undo_substitution(substitution_id, session=session)
    assert exc_info.value.status_code == 400


async def test_chained_substitution_undo_returns_only_remaining_qty(session):
    product = Product(name="Shirt", variant_attribute1_name="Color")
    session.add(product)
    await session.flush()
    green = ProductVariant(product_id=product.id, variant_name="Green", attribute1_value="Green", current_stock=10)
    blue = ProductVariant(product_id=product.id, variant_name="Blue", attribute1_value="Blue", current_stock=10)
    red = ProductVariant(product_id=product.id, variant_name="Red", attribute1_value="Red", current_stock=10)
    session.add_all([green, blue, red])
    await session.commit()

    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=5, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    step1 = await substitute_line(
        original.id, SubstituteLineRequest(variant_id=blue.id, qty=4, reason=None), session=session
    )
    blue_line = next(l for l in step1.lines if l.variant_id == blue.id)

    step2 = await substitute_line(
        blue_line.id, SubstituteLineRequest(variant_id=red.id, qty=3, reason=None), session=session
    )
    lines = {l.variant_id: l for l in step2.lines}
    assert lines[blue.id].ordered_qty == 1  # 4 - 3 substituted further onto red
    assert lines[red.id].ordered_qty == 3

    first_substitution_id = lines[blue.id].substituted_from.substitution_id
    result = await undo_substitution(first_substitution_id, session=session)
    lines = {l.variant_id: l for l in result.lines}
    # Only blue's remaining 1 unit comes back onto green's own remaining 1 — the 3 already
    # moved on to red stay put.
    assert lines[green.id].ordered_qty == 2
    assert lines[blue.id].ordered_qty == 0
    assert lines[red.id].ordered_qty == 3


async def test_substitute_rejects_variant_from_a_different_product(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=10)
    other_product = Product(name="Other", variant_attribute1_name="Color")
    session.add(other_product)
    await session.flush()
    other_variant = ProductVariant(product_id=other_product.id, variant_name="Purple", attribute1_value="Purple", current_stock=5)
    session.add(other_variant)
    await session.commit()

    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=3, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        await substitute_line(
            original.id, SubstituteLineRequest(variant_id=other_variant.id, qty=1, reason=None), session=session
        )
    assert exc_info.value.status_code == 400


async def test_substitute_rejects_same_variant(session):
    product, green, blue = await _product_with_variants(session, green_stock=10, blue_stock=10)
    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(variant_id=green.id, ordered_qty=3, unit_price=Decimal("10"))]),
        session=session,
    )
    original = order_read.lines[0]

    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc_info:
        await substitute_line(
            original.id, SubstituteLineRequest(variant_id=green.id, qty=1, reason=None), session=session
        )
    assert exc_info.value.status_code == 400
