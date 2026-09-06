"""The Kitting section's per-material cost column reacts to override quantities, and shows
both bases: effective (forward-looking, ordered) and consumed (realised, shipped). They
converge once the order has fully shipped, at which point consumed_cost_total is exactly
OrderRead.kitting_cogs.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.kitting import ProductKittingMaterial
from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.order import Order
from app.models.product import Product
from app.routers.orders import _get_order_with_lines, _serialize_one, create_order, replace_kitting_overrides
from app.schemas.kitting import OrderKittingOverrideLine
from app.schemas.order import OrderCreate, OrderLineInput
from app.services import allocation, listing_push
from app.services.costing import recompute_material
from app.services.kitting import get_order_kitting_summary
from .conftest import received_purchase

_STOCKED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _order_with_kitting(session, qty: int, unit_cost: Decimal = Decimal("2.00")) -> tuple[Order, Material]:
    box = Material(name="Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each)
    session.add(box)
    await session.flush()
    await received_purchase(session, box.id, Decimal(100), unit_cost * 100, received_at=_STOCKED_AT)
    await session.commit()
    await recompute_material(session, box.id)

    product = Product(name="Product", sku="SKU-SUM", current_stock=20, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=1))
    await session.commit()

    order_read = await create_order(
        OrderCreate(lines=[OrderLineInput(product_id=product.id, ordered_qty=qty, unit_price=Decimal("10"))]),
        session=session,
    )
    order = await session.get(Order, order_read.id)
    order.subtotal = Decimal(10) * qty
    order.shipping_charged = Decimal("0")
    await session.commit()
    return order, box


async def test_summary_cost_reacts_to_override(session):
    order, box = await _order_with_kitting(session, qty=4)

    # The auto multi-unit override already caps this at one box for the whole order.
    summary = await get_order_kitting_summary(session, order.id)
    line = summary.lines[0]
    assert line.auto_qty == Decimal(4)  # pre-override: one box per unit
    assert line.effective_qty == Decimal(1)
    assert line.effective_cost == Decimal("2.00")
    assert summary.effective_cost_total == Decimal("2.00")
    assert line.unit_cost_is_frozen is False  # nothing consumed yet — live avg_unit_cost

    await replace_kitting_overrides(
        order.id,
        [OrderKittingOverrideLine(material_id=box.id, qty_required=Decimal(3), replaces_material_id=None)],
        session=session,
    )

    summary = await get_order_kitting_summary(session, order.id)
    line = summary.lines[0]
    assert line.auto_qty == Decimal(4)  # unchanged — the override is what moved
    assert line.effective_qty == Decimal(3)
    assert line.effective_cost == Decimal("6.00")
    assert summary.effective_cost_total == Decimal("6.00")


async def test_effective_and_consumed_costs_converge_when_fully_shipped(session):
    order, _ = await _order_with_kitting(session, qty=3)

    summary = await get_order_kitting_summary(session, order.id)
    assert summary.effective_cost_total == Decimal("2.00")
    assert summary.consumed_cost_total == Decimal(0)  # nothing shipped

    await allocation.ship_order(session, await session.get(Order, order.id))
    await session.commit()

    summary = await get_order_kitting_summary(session, order.id)
    assert summary.effective_cost_total == summary.consumed_cost_total == Decimal("2.00")
    assert summary.lines[0].unit_cost_is_frozen is True

    read = await _serialize_one(session, await _get_order_with_lines(session, order.id))
    assert Decimal(read.kitting_cogs) == summary.consumed_cost_total


async def test_effective_never_drops_below_what_was_consumed(session):
    """An additive override that's been consumed and then lowered to 0 (or removed) must
    still count towards the effective total — otherwise the editor's headline "Kitting
    cost" understates and disagrees with COGS on a fully-shipped order, even though its
    own copy promises the two converge."""
    order, box = await _order_with_kitting(session, qty=2)

    label = Material(name="Label", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each)
    session.add(label)
    await session.flush()
    await received_purchase(session, label.id, Decimal(100), Decimal("55.00"), received_at=_STOCKED_AT)
    await session.commit()
    await recompute_material(session, label.id)

    # Add the label as an extra kitting line for this order (additive: not in any BOM).
    await replace_kitting_overrides(
        order.id,
        [
            OrderKittingOverrideLine(material_id=box.id, qty_required=Decimal(1), replaces_material_id=None),
            OrderKittingOverrideLine(material_id=label.id, qty_required=Decimal(1), replaces_material_id=None),
        ],
        session=session,
    )

    await allocation.ship_order(session, await session.get(Order, order.id))
    await session.commit()

    summary = await get_order_kitting_summary(session, order.id)
    assert summary.effective_cost_total == summary.consumed_cost_total

    # Now zero the label override, as if the user cleared the extra line after shipping.
    await replace_kitting_overrides(
        order.id,
        [OrderKittingOverrideLine(material_id=box.id, qty_required=Decimal(1), replaces_material_id=None)],
        session=session,
    )

    summary = await get_order_kitting_summary(session, order.id)
    label_line = next(l for l in summary.lines if l.material_id == label.id)
    assert label_line.consumed_qty == Decimal(1)
    assert label_line.effective_qty == Decimal(1)  # floored at consumed, not 0
    assert label_line.effective_cost == label_line.consumed_cost
    assert summary.effective_cost_total == summary.consumed_cost_total
