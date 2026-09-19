"""Replacement parcels recorded by hand (services/order_parcels).

Recording a parcel is the moment it leaves: product stock and packaging material drop
right then, with costs frozen, and profit picks up both the parcel's postage and its cost
of goods on top of the original shipment's. Deleting it is the exact reverse. None of it
goes through OrderLine or the kitting ledger — see models/order_parcel.py for why.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.material import Material, MaterialAdjustment, MaterialUnit
from app.models.order import Order, OrderLine, OrderStatus
from app.models.order_parcel import (
    OrderPostageCharge,
    OrderReplacementParcel,
    OrderReplacementParcelItem,
    PostageChargeSource,
    ReplacementParcelReason,
)
from app.models.product import Product, ProductMaterial
from app.models.product_stock_event import ProductStockEvent, ProductStockEventType
from app.models.shipping_profile import ShippingProfile
from app.routers.orders import (
    create_replacement_parcel,
    delete_replacement_parcel,
    update_replacement_parcel,
)
from app.routers.products import list_stock_history
from app.schemas.order_parcel import ReplacementParcelCreate, ReplacementParcelItemInput, ReplacementParcelUpdate
from app.services import listing_push, material_categories
from app.services.costing import recompute_material

from .conftest import received_purchase


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _stocked_material(session, category: str, name: str, unit: MaterialUnit, qty, total_cost) -> Material:
    row = await material_categories.find_or_create(session, category)
    material = Material(
        name=name, category=material_categories.legacy_value_for(row.name), category_id=row.id, unit=unit
    )
    session.add(material)
    await session.flush()
    await received_purchase(session, material.id, qty, Decimal(total_cost))
    await recompute_material(session, material.id)
    return material


async def _shipped_order(session, *, product: Product, subtotal="20.00", postage_snapshot="3.65") -> Order:
    """An order that has already gone out: one line, fully shipped, postage frozen."""
    profile = ShippingProfile(name="Small", price=Decimal("3.60"), cost_manual=Decimal(postage_snapshot))
    session.add(profile)
    await session.flush()
    order = Order(
        status=OrderStatus.shipped,
        order_placed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        shipped_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        subtotal=Decimal(subtotal),
        grand_total=Decimal(subtotal),
        shipping_charged=Decimal("0"),
        currency="GBP",
        shipping_profile_id=profile.id,
        shipping_cost_snapshot=Decimal(postage_snapshot),
    )
    session.add(order)
    await session.flush()
    session.add(
        OrderLine(
            order_id=order.id,
            product_id=product.id,
            ordered_qty=1,
            allocated_qty=1,
            shipped_qty=1,
            unit_price=Decimal(subtotal),
            cost_per_unit_snapshot=Decimal("2.00"),
        )
    )
    await session.commit()
    return order


async def _product_with_bom(session, *, stock=5) -> tuple[Product, Material]:
    # 10 g of a £0.02/g filament per unit → build cost 0.20 per unit.
    filament = await _stocked_material(session, "filament", "Filament", MaterialUnit.g, 10000, "200")
    product = Product(name="Widget", sku="WID", current_stock=stock, allocated_qty=0)
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=filament.id, qty_required=Decimal("10")))
    await session.commit()
    return product, filament


async def test_recording_a_parcel_deducts_stock_and_freezes_costs(session, pushes):
    product, _filament = await _product_with_bom(session, stock=5)
    box = await _stocked_material(session, "packaging", "Box", MaterialUnit.each, 10, "5.00")  # 0.50 each
    order = await _shipped_order(session, product=product)

    read = await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            reason=ReplacementParcelReason.missing_from_order,
            postage_cost=Decimal("2.50"),
            tracking_number="RM123",
            items=[
                ReplacementParcelItemInput(product_id=product.id, qty=Decimal(1)),
                ReplacementParcelItemInput(material_id=box.id, qty=Decimal(1)),
            ],
        ),
        session=session,
    )

    await session.refresh(product)
    await session.refresh(box)
    assert product.current_stock == 4
    assert Decimal(box.current_qty) == Decimal(9)

    event = (
        await session.execute(
            select(ProductStockEvent).where(ProductStockEvent.event_type == ProductStockEventType.replacement_parcel)
        )
    ).scalar_one()
    assert event.qty_delta == -1 and event.running_balance == 4
    assert event.source_replacement_parcel_id == read.replacement_parcels[0].id
    adjustment = (await session.execute(select(MaterialAdjustment).where(MaterialAdjustment.order_id == order.id))).scalar_one()
    assert Decimal(adjustment.qty_delta) == Decimal(-1)
    # Free stock genuinely dropped (nothing was reserved first), so the listing push fires.
    assert ("owner", product) in pushes
    # ...and the product's Stock history links the event back to the order.
    [history] = [e for e in await list_stock_history(product.id, session=session) if e.qty_delta == -1]
    assert history.event_type == ProductStockEventType.replacement_parcel
    assert history.order_id == order.id
    assert history.source_replacement_parcel_id == read.replacement_parcels[0].id

    [parcel] = read.replacement_parcels
    assert parcel.reason == ReplacementParcelReason.missing_from_order
    assert parcel.needs_review is False
    assert parcel.effective_postage == Decimal("2.50")
    costs = {(i.product_id, i.material_id): i.unit_cost_snapshot for i in parcel.items}
    assert costs[(product.id, None)] == Decimal("0.20")
    assert costs[(None, box.id)] == Decimal("0.50")
    assert parcel.items_cost == Decimal("0.70")

    # Profit: 20.00 - 3.65 postage - 2.00 COGS - 2.50 replacement postage - 0.70 replacement COGS.
    assert read.replacement_postage == Decimal("2.50")
    assert read.replacement_cogs == Decimal("0.70")
    assert read.net_profit == Decimal("11.15")
    assert read.postage_cost_effective == Decimal("3.65")
    assert read.postage_cost_actual is None

    # The snapshot is frozen: a dearer box later doesn't move the recorded cost.
    await received_purchase(session, box.id, 10, Decimal("50.00"))
    await recompute_material(session, box.id)
    await session.commit()
    item = (
        await session.execute(select(OrderReplacementParcelItem).where(OrderReplacementParcelItem.material_id == box.id))
    ).scalar_one()
    assert Decimal(item.unit_cost_snapshot) == Decimal("0.50")


async def test_insufficient_free_stock_rejects_the_whole_parcel(session):
    """Allocated stock belongs to other orders; eating into it is a 400, and nothing at
    all is written — not the parcel, not the material item that came before the failing
    product item."""
    product, _filament = await _product_with_bom(session, stock=3)
    product.allocated_qty = 2
    box = await _stocked_material(session, "packaging", "Box", MaterialUnit.each, 10, "5.00")
    order = await _shipped_order(session, product=product)

    with pytest.raises(HTTPException) as excinfo:
        await create_replacement_parcel(
            order.id,
            ReplacementParcelCreate(
                items=[
                    ReplacementParcelItemInput(material_id=box.id, qty=Decimal(1)),
                    ReplacementParcelItemInput(product_id=product.id, qty=Decimal(2)),
                ]
            ),
            session=session,
        )
    assert excinfo.value.status_code == 400
    assert "only 1 free" in excinfo.value.detail
    await session.rollback()

    assert (await session.execute(select(OrderReplacementParcel))).scalar_one_or_none() is None
    await session.refresh(box)
    assert Decimal(box.current_qty) == Decimal(10)


async def test_cancelled_order_refuses_a_parcel(session):
    product, _ = await _product_with_bom(session)
    order = await _shipped_order(session, product=product)
    order.status = OrderStatus.cancelled
    await session.commit()

    with pytest.raises(HTTPException) as excinfo:
        await create_replacement_parcel(
            order.id,
            ReplacementParcelCreate(items=[ReplacementParcelItemInput(product_id=product.id, qty=Decimal(1))]),
            session=session,
        )
    assert excinfo.value.status_code == 400


def test_a_parcel_needs_items_or_postage():
    with pytest.raises(ValueError):
        ReplacementParcelCreate(reason=ReplacementParcelReason.other)
    # A postage-only resend is fine.
    ReplacementParcelCreate(postage_cost=Decimal("1.00"))
    with pytest.raises(ValueError):
        ReplacementParcelItemInput(product_id=1, qty=Decimal("1.5"))
    with pytest.raises(ValueError):
        ReplacementParcelItemInput(product_id=1, material_id=2, qty=Decimal(1))


async def test_deleting_a_parcel_restocks_everything_and_keeps_the_label(session):
    product, _ = await _product_with_bom(session, stock=5)
    box = await _stocked_material(session, "packaging", "Box", MaterialUnit.each, 10, "5.00")
    order = await _shipped_order(session, product=product)
    # A synced second label, as apply_postage_charges would have stored it.
    for seq, ext in ((1, "L1"), (2, "L2")):
        session.add(
            OrderPostageCharge(
                order_id=order.id,
                platform="etsy",
                source=PostageChargeSource.etsy_ledger,
                external_id=ext,
                amount=Decimal("3.10"),
                currency="GBP",
                sequence=seq,
            )
        )
    await session.commit()
    second = (await session.execute(select(OrderPostageCharge).where(OrderPostageCharge.external_id == "L2"))).scalar_one()

    read = await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            reason=ReplacementParcelReason.lost_in_transit,
            postage_charge_id=second.id,
            items=[
                ReplacementParcelItemInput(product_id=product.id, qty=Decimal(2)),
                ReplacementParcelItemInput(material_id=box.id, qty=Decimal(1)),
            ],
        ),
        session=session,
    )
    [parcel] = read.replacement_parcels
    # Linked label wins as the parcel's postage; the first label replaces the estimate.
    assert parcel.effective_postage == Decimal("3.10")
    assert parcel.postage_charge is not None and parcel.postage_charge.sequence == 2
    assert read.postage_cost_actual == Decimal("3.10")
    assert read.postage_cost_effective == Decimal("3.10")
    # 20 - 3.10 (actual first label) - 2.00 - 3.10 (second label) - (2 × 0.20 + 0.50)
    assert read.net_profit == Decimal("10.90")

    read = await delete_replacement_parcel(parcel.id, session=session)
    assert read.replacement_parcels == []
    await session.refresh(product)
    await session.refresh(box)
    assert product.current_stock == 5
    assert Decimal(box.current_qty) == Decimal(10)
    reversal = (
        await session.execute(
            select(ProductStockEvent).where(
                ProductStockEvent.event_type == ProductStockEventType.replacement_parcel_reversal
            )
        )
    ).scalar_one()
    assert reversal.qty_delta == 2 and reversal.running_balance == 5
    # The label is marketplace truth: unlinked, still there, still charged.
    await session.refresh(second)
    assert second.replacement_parcel_id is None
    assert len(read.postage_charges) == 2
    assert read.replacement_postage == Decimal("3.10")
    # 20 - 3.10 (first label) - 2.00 (postage charged) - 3.10 (the unlinked resend label)
    assert read.net_profit == Decimal("11.80")


async def test_first_label_cannot_be_linked_to_a_parcel(session):
    product, _ = await _product_with_bom(session)
    order = await _shipped_order(session, product=product)
    session.add(
        OrderPostageCharge(
            order_id=order.id,
            platform="ebay",
            source=PostageChargeSource.ebay_shipping_label,
            external_id="T1",
            amount=Decimal("3.20"),
            sequence=1,
        )
    )
    await session.commit()
    first = (await session.execute(select(OrderPostageCharge))).scalar_one()

    with pytest.raises(HTTPException) as excinfo:
        await create_replacement_parcel(
            order.id, ReplacementParcelCreate(postage_charge_id=first.id), session=session
        )
    assert excinfo.value.status_code == 400


async def test_update_edits_metadata_only(session):
    product, _ = await _product_with_bom(session)
    order = await _shipped_order(session, product=product)
    read = await create_replacement_parcel(
        order.id,
        ReplacementParcelCreate(
            reason=ReplacementParcelReason.other,
            postage_cost=Decimal("1.00"),
            items=[ReplacementParcelItemInput(product_id=product.id, qty=Decimal(1))],
        ),
        session=session,
    )
    [parcel] = read.replacement_parcels

    read = await update_replacement_parcel(
        parcel.id,
        ReplacementParcelUpdate(reason=ReplacementParcelReason.faulty_item, postage_cost=Decimal("2.25"), notes="Resent"),
        session=session,
    )
    [parcel] = read.replacement_parcels
    assert parcel.reason == ReplacementParcelReason.faulty_item
    assert parcel.effective_postage == Decimal("2.25")
    assert parcel.notes == "Resent"
    assert len(parcel.items) == 1
    await session.refresh(product)
    assert product.current_stock == 4
