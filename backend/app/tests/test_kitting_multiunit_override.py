"""Item 1: multi-unit orders auto-default packaging-category kitting materials to an
override quantity of 1 for the whole order, whether the extra units come from several
distinct lines or a single line with qty>1. Driven end-to-end through
`order_sync.commit_sync`, since that's the path a real Etsy/eBay order takes.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.kitting import OrderKittingOverride, ProductKittingMaterial
from app.models.listing import ListingPlatform
from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.order import Order, OrderLine
from app.models.product import Product
from app.services import listing_push, order_sync
from app.services.platforms.base import ExternalOrder, ExternalOrderLine, PaymentState

from .conftest import make_order

import pytest


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    # Depends on `pushes` purely for ordering — this patch must apply after conftest's,
    # since both target the same attribute.
    """conftest's `pushes` fixture stubs enqueue_for_material with a plain (non-async)
    lambda — fine for every existing test, none of which exercise a kitting BOM, but
    reconcile_order_kitting always awaits this call once a kitting reservation actually
    changes. Re-patch it here with an async stub so these tests can reach that path."""

    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _packaging_material(session, name: str = "Box") -> Material:
    material = Material(name=name, category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, current_qty=100)
    session.add(material)
    await session.commit()
    return material


async def _product_with_kitting(session, material: Material, sku: str, qty_required: int = 1) -> Product:
    product = Product(name=f"Product {sku}", sku=sku, current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=material.id, qty_required=qty_required))
    await session.commit()
    return product


def _multiline_order(order_id: str, skus: list[str]) -> ExternalOrder:
    now = datetime.now(timezone.utc)
    return ExternalOrder(
        external_order_id=order_id,
        buyer_name=None,
        buyer_note=None,
        placed_at=now,
        last_modified=now,
        is_cancelled=False,
        is_shipped=False,
        lines=[
            ExternalOrderLine(
                external_line_id=f"{order_id}-L{i}", sku=sku, qty=1, unit_price="10.00", currency="GBP"
            )
            for i, sku in enumerate(skus)
        ],
        raw={},
        payment_state=PaymentState.settled,
        financials_enriched=True,
    )


async def test_multiline_order_gets_auto_override_of_one(session, session_factory, connection, use_adapter, pushes):
    material = await _packaging_material(session)
    await _product_with_kitting(session, material, "SKU-A")
    await _product_with_kitting(session, material, "SKU-B")
    use_adapter([_multiline_order("R-MULTI", ["SKU-A", "SKU-B"])])

    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    assert override.material_id == material.id
    assert Decimal(override.qty_required) == Decimal(1)


async def test_single_line_qty_gt_1_also_gets_auto_override_of_one(
    session, session_factory, connection, use_adapter, pushes
):
    material = await _packaging_material(session)
    await _product_with_kitting(session, material, "SKU-A")
    use_adapter([make_order("R-QTY3", sku="SKU-A", qty=3)])

    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    assert Decimal(override.qty_required) == Decimal(1)


async def test_single_unit_order_gets_no_auto_override(session, session_factory, connection, use_adapter, pushes):
    material = await _packaging_material(session)
    await _product_with_kitting(session, material, "SKU-A")
    use_adapter([make_order("R-ONE", sku="SKU-A", qty=1)])

    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    overrides = list(
        (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id)))
        .scalars()
    )
    assert overrides == []


async def test_manual_override_survives_a_later_reallocation(
    session, session_factory, connection, use_adapter, pushes
):
    """A deliberate raise (a genuinely multi-box order) must never be silently reset back
    to 1 by a later re-allocation pass touching the same order."""
    from app.services import allocation

    material = await _packaging_material(session)
    await _product_with_kitting(session, material, "SKU-A")
    await _product_with_kitting(session, material, "SKU-B")
    use_adapter([_multiline_order("R-MANUAL", ["SKU-A", "SKU-B"])])
    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    override.qty_required = 2  # user manually raises it — genuinely needs 2 boxes
    await session.commit()

    await allocation.allocate_order(session, order, source="manual-reallocate")
    await session.commit()

    await session.refresh(override)
    assert Decimal(override.qty_required) == Decimal(2)


async def test_non_packaging_kitting_material_is_not_auto_overridden(
    session, session_factory, connection, use_adapter, pushes
):
    """Only packaging-category kitting materials get the one-box-either-way default —
    anything else on the kitting BOM keeps scaling with qty as before."""
    material = Material(name="Poly bag", category=LegacyMaterialCategory.other, unit=MaterialUnit.each, current_qty=100)
    session.add(material)
    await session.commit()
    await _product_with_kitting(session, material, "SKU-A")
    await _product_with_kitting(session, material, "SKU-B")
    use_adapter([_multiline_order("R-OTHER", ["SKU-A", "SKU-B"])])

    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    overrides = list(
        (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id)))
        .scalars()
    )
    assert overrides == []
