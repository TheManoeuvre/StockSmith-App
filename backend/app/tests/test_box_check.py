"""Box check: an order whose items need two different parcel boxes is held until a person says
which was used, and a shipped order that already went out with several can be corrected.

Orders arrive through `order_sync.commit_sync`, as a real one does, so the auto one-per-order
override that creates the problem in the first place is exercised rather than faked.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.kitting import OrderKittingAllocation, OrderKittingOverride, ProductKittingMaterial
from app.models.listing import ListingPlatform
from app.models.material import Material, MaterialAdjustment, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.routers._reference_crud import patch_row
from app.schemas.box_check import BoxCheckConfirm, BoxSelection
from app.schemas.material_category import MaterialCategoryUpdate
from app.services import allocation, box_check, listing_push, material_categories, order_sync
from app.services.kitting import get_kitting_cogs_by_order

from app.services.costing import recompute_material

from .conftest import make_order, received_purchase
from .test_kitting_multiunit_override import _multiline_order


_STOCKED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _category(session, name: str = "Boxes", flagged: bool = True) -> MaterialCategory:
    """A packaging-style category: capped to one per order, optionally flagged for the box check."""
    category = await material_categories.find_or_create(session, name)
    category.auto_kitting_per_order = True
    category.kitting_check_duplicates = flagged
    await session.commit()
    return category


async def _packaging(session, name: str, category: MaterialCategory | None, cost: str = "1.00") -> Material:
    """A material in `category`, or in the stock Packaging category (unflagged — where labels and
    tape live) when None."""
    category = category or await material_categories.find_or_create(session, "packaging")
    material = Material(
        name=name,
        category=material_categories.legacy_value_for(category.name),
        category_id=category.id,
        unit=MaterialUnit.each,
    )
    session.add(material)
    await session.flush()
    # Stocked by a real received purchase, dated well in the past: current_qty/avg_unit_cost are
    # derived columns that recompute_material rebuilds from history, and shipping or restocking
    # goes through it, so setting them directly would be wiped (to a negative) on first use.
    await received_purchase(session, material.id, Decimal(10), Decimal(cost) * 10, received_at=_STOCKED_AT)
    await session.commit()
    await recompute_material(session, material.id)
    await session.commit()
    return material


async def _product(session, sku: str, *materials: Material) -> Product:
    product = Product(name=f"Product {sku}", sku=sku, current_stock=10, allocated_qty=0)
    session.add(product)
    await session.flush()
    for material in materials:
        session.add(ProductKittingMaterial(product_id=product.id, material_id=material.id, qty_required=1))
    await session.commit()
    return product


async def _sync(session, use_adapter, external_id: str, skus: list[str], shipped: bool = False) -> Order:
    ext = _multiline_order(external_id, skus)
    ext.is_shipped = shipped
    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)
    # commit_sync works in its own sessions, so anything this session loaded earlier (the
    # materials, with allocated_qty as it stood before the order reserved packaging) is stale —
    # and reconcile_order_kitting mutates whatever the identity map hands it.
    for instance in list(session.identity_map.values()):
        await session.refresh(instance)
    return (await session.execute(select(Order).where(Order.external_order_id == external_id))).scalar_one()


async def _two_box_order(session, use_adapter, *, shipped: bool = False, flagged: bool = True):
    boxes = await _category(session, flagged=flagged)
    small = await _packaging(session, "Small Box", boxes, "0.40")
    large = await _packaging(session, "Large Box", boxes, "0.90")
    await _product(session, "SKU-A", small)
    await _product(session, "SKU-B", large)
    order = await _sync(session, use_adapter, "R-TWO", ["SKU-A", "SKU-B"], shipped=shipped)
    return order, small, large


async def _refresh(session, order: Order) -> Order:
    await session.refresh(order)
    return order


def _confirm(*boxes: tuple[Material, str], return_to_stock: bool = True) -> BoxCheckConfirm:
    return BoxCheckConfirm(
        boxes=[BoxSelection(material_id=m.id, qty=Decimal(q)) for m, q in boxes], return_to_stock=return_to_stock
    )


async def _ledger(session, order: Order, material: Material) -> OrderKittingAllocation | None:
    return (
        await session.execute(
            select(OrderKittingAllocation).where(
                OrderKittingAllocation.order_id == order.id, OrderKittingAllocation.material_id == material.id
            )
        )
    ).scalar_one_or_none()


# --- Detection ----------------------------------------------------------------------------------


async def test_two_different_boxes_need_a_check_and_block_shipping(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)

    assert await box_check.order_needs_box_check(session, order) is True
    with pytest.raises(HTTPException) as exc:
        await allocation.ship_order(session, order)
    assert exc.value.status_code == 409
    assert "parcel box" in exc.value.detail
    await session.refresh(order)
    assert order.status != OrderStatus.shipped


async def test_same_box_on_both_items_needs_no_check(session, session_factory, connection, use_adapter):
    boxes = await _category(session)
    box = await _packaging(session, "Shared Box", boxes)
    await _product(session, "SKU-A", box)
    await _product(session, "SKU-B", box)
    order = await _sync(session, use_adapter, "R-SAME", ["SKU-A", "SKU-B"])

    assert await box_check.order_needs_box_check(session, order) is False
    await allocation.ship_order(session, order)
    assert order.status == OrderStatus.shipped


@pytest.mark.parametrize("own_category", [True, False])
async def test_boxes_in_an_unflagged_category_are_ignored(
    session, session_factory, connection, use_adapter, own_category
):
    category = await _category(session, "Unchecked Boxes", flagged=False) if own_category else None
    a = await _packaging(session, "Box A", category)
    b = await _packaging(session, "Box B", category)
    await _product(session, "SKU-A", a)
    await _product(session, "SKU-B", b)
    order = await _sync(session, use_adapter, "R-UNFLAGGED", ["SKU-A", "SKU-B"])

    assert await box_check.order_needs_box_check(session, order) is False


async def test_flagged_box_alongside_a_label_still_needs_only_one_box(
    session, session_factory, connection, use_adapter
):
    boxes = await _category(session)
    box = await _packaging(session, "Only Box", boxes)
    label_a = await _packaging(session, "Label A", None)
    label_b = await _packaging(session, "Label B", None)
    await _product(session, "SKU-A", box, label_a)
    await _product(session, "SKU-B", box, label_b)
    order = await _sync(session, use_adapter, "R-LABELS", ["SKU-A", "SKU-B"])

    assert await box_check.order_needs_box_check(session, order) is False


async def test_flag_can_be_set_through_the_reference_data_patch(session):
    row = await _category(session, "Boxes", flagged=False)

    updated = await patch_row(
        session, MaterialCategory, row.id, MaterialCategoryUpdate(name="Boxes", kitting_check_duplicates=True)
    )
    assert updated.kitting_check_duplicates is True

    # Renaming alone must not clear it.
    renamed = await patch_row(session, MaterialCategory, row.id, MaterialCategoryUpdate(name="Parcel boxes"))
    assert renamed.kitting_check_duplicates is True


# --- Confirming before shipping -----------------------------------------------------------------


async def test_confirming_one_box_unblocks_shipping_and_consumes_only_that_box(
    session, session_factory, connection, use_adapter
):
    order, small, large = await _two_box_order(session, use_adapter)

    await box_check.confirm_boxes(session, order, _confirm((large, "1")))
    await session.commit()

    assert await box_check.order_needs_box_check(session, order) is False
    await allocation.ship_order(session, order)
    await session.commit()

    await session.refresh(small)
    await session.refresh(large)
    assert Decimal(large.current_qty) == Decimal(9)
    assert Decimal(small.current_qty) == Decimal(10)
    assert Decimal(small.allocated_qty) == Decimal(0)
    kitting = await get_kitting_cogs_by_order(session, [order.id])
    assert kitting[order.id] == Decimal("0.90")


async def test_more_than_one_box_can_be_approved(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)

    await box_check.confirm_boxes(session, order, _confirm((small, "1"), (large, "2")))
    await session.commit()
    await allocation.ship_order(session, order)
    await session.commit()

    await session.refresh(small)
    await session.refresh(large)
    assert Decimal(small.current_qty) == Decimal(9)
    assert Decimal(large.current_qty) == Decimal(8)


async def test_confirmation_leaves_other_overrides_alone(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)
    label = await _packaging(session, "Loose Label", None)
    session.add(OrderKittingOverride(order_id=order.id, material_id=label.id, qty_required=3))
    await session.commit()

    await box_check.confirm_boxes(session, order, _confirm((small, "1")))
    await session.commit()

    overrides = {
        o.material_id: Decimal(o.qty_required)
        for o in (await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))).scalars()
    }
    assert overrides[label.id] == Decimal(3)
    assert overrides[small.id] == Decimal(1)
    assert overrides[large.id] == Decimal(0)


async def test_only_flagged_materials_can_be_chosen(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)
    tape = await _packaging(session, "Tape", None)

    with pytest.raises(HTTPException) as exc:
        await box_check.confirm_boxes(session, order, _confirm((tape, "1")))
    assert exc.value.status_code == 400


async def test_changing_the_items_after_confirming_asks_again(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)
    await box_check.confirm_boxes(session, order, _confirm((small, "1")))
    await session.commit()
    assert await box_check.order_needs_box_check(session, order) is False

    # A third item arrives that needs a third box: the candidates no longer match what was confirmed.
    boxes = (await session.execute(select(MaterialCategory).where(MaterialCategory.name == "Boxes"))).scalar_one()
    huge = await _packaging(session, "Huge Box", boxes)
    third = await _product(session, "SKU-C", huge)
    session.add(OrderLine(order_id=order.id, product_id=third.id, sku="SKU-C", ordered_qty=1, currency="GBP"))
    await session.commit()

    assert await box_check.order_needs_box_check(session, order) is True


async def test_removing_an_item_down_to_one_box_needs_no_check(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)
    line_b = (
        await session.execute(
            select(OrderLine).join(Product, Product.id == OrderLine.product_id).where(Product.sku == "SKU-B")
        )
    ).scalar_one()
    await allocation.apply_ordered_qty_change(session, line_b, 0, allow_zero=True)
    await session.commit()

    assert await box_check.order_needs_box_check(session, order) is False


# --- Marketplace sync ---------------------------------------------------------------------------


async def test_sync_does_not_auto_ship_an_order_awaiting_a_box_check(
    session, session_factory, connection, use_adapter
):
    order, small, large = await _two_box_order(session, use_adapter, shipped=True)

    await session.refresh(order)
    assert order.status != OrderStatus.shipped
    assert order.sync_issue is not None and order.sync_issue.startswith(box_check.BOX_CHECK_SYNC_ISSUE_PREFIX)
    await session.refresh(small)
    await session.refresh(large)
    assert Decimal(small.current_qty) == Decimal(10)
    assert Decimal(large.current_qty) == Decimal(10)


async def test_confirming_clears_the_sync_flag(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter, shipped=True)
    await session.refresh(order)

    await box_check.confirm_boxes(session, order, _confirm((small, "1")))
    await session.commit()
    await session.refresh(order)

    assert order.sync_issue is None
    await allocation.ship_order(session, order)
    assert order.status == OrderStatus.shipped


# --- Already-shipped orders ---------------------------------------------------------------------


async def _shipped_with_both_boxes(session, use_adapter):
    """Approve both boxes then ship — the state an order already in the database is in."""
    order, small, large = await _two_box_order(session, use_adapter)
    await box_check.confirm_boxes(session, order, _confirm((small, "1"), (large, "1")))
    await session.commit()
    await allocation.ship_order(session, order)
    await session.commit()
    # An order shipped before this feature has no confirmation either.
    order.box_check_signature = None
    order.box_check_confirmed_at = None
    await session.commit()
    return order, small, large


async def test_shipped_order_with_two_boxes_is_flagged_for_review(session, session_factory, connection, use_adapter):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)

    flags = await box_check.get_box_check_flags(session, [order])
    assert flags[order.id] is True
    assert order.id in await box_check.order_ids_needing_box_check(session)


async def test_correcting_a_shipped_order_returns_the_unused_box_and_cuts_packaging_cost(
    session, session_factory, connection, use_adapter
):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)
    before = (await get_kitting_cogs_by_order(session, [order.id]))[order.id]
    assert before == Decimal("1.30")

    await box_check.confirm_boxes(session, order, _confirm((large, "1")))
    await session.commit()

    await session.refresh(small)
    await session.refresh(large)
    assert Decimal(small.current_qty) == Decimal(10)  # 9 after shipping, +1 returned
    assert Decimal(large.current_qty) == Decimal(9)  # unchanged
    assert Decimal((await _ledger(session, order, small)).consumed_qty) == Decimal(0)
    assert Decimal((await _ledger(session, order, large)).consumed_qty) == Decimal(1)
    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("0.90")
    assert (await box_check.get_box_check_flags(session, [order]))[order.id] is False


async def test_correction_keeps_the_cost_frozen_at_shipping(session, session_factory, connection, use_adapter):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)
    await received_purchase(session, large.id, Decimal(5), Decimal("25.00"), received_at=datetime.now(timezone.utc))
    await session.commit()
    await recompute_material(session, large.id)
    await session.commit()
    await session.refresh(large)
    assert Decimal(large.avg_unit_cost) > Decimal("0.90")

    await box_check.confirm_boxes(session, order, _confirm((large, "1")))
    await session.commit()

    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("0.90")


async def test_correction_can_scrap_instead_of_restocking(session, session_factory, connection, use_adapter):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)

    await box_check.confirm_boxes(session, order, _confirm((large, "1"), return_to_stock=False))
    await session.commit()

    await session.refresh(small)
    assert Decimal(small.current_qty) == Decimal(9)
    assert Decimal((await _ledger(session, order, small)).consumed_qty) == Decimal(0)


async def test_correction_can_use_more_of_a_box(session, session_factory, connection, use_adapter):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)

    await box_check.confirm_boxes(session, order, _confirm((small, "1"), (large, "3")))
    await session.commit()

    await session.refresh(large)
    assert Decimal(large.current_qty) == Decimal(7)  # 9 after shipping, 2 more taken
    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("0.40") + Decimal("2.70")


async def test_correction_refuses_when_stock_is_short_and_changes_nothing(
    session, session_factory, connection, use_adapter
):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)
    order_id = order.id
    # Leave just one large box on the shelf: take the rest out with a real adjustment.
    session.add(MaterialAdjustment(material_id=large.id, qty_delta=Decimal(-8), reason="test"))
    await session.commit()
    await recompute_material(session, large.id)
    await session.commit()
    await session.refresh(large)
    assert Decimal(large.current_qty) == Decimal(1)

    with pytest.raises(HTTPException) as exc:
        await box_check.confirm_boxes(session, order, _confirm((small, "1"), (large, "4")))
    assert exc.value.status_code == 400
    await session.rollback()

    await session.refresh(small)
    await session.refresh(large)
    assert Decimal(large.current_qty) == Decimal(1)
    ledger = (
        await session.execute(
            select(OrderKittingAllocation).where(
                OrderKittingAllocation.order_id == order_id, OrderKittingAllocation.material_id == large.id
            )
        )
    ).scalar_one()
    assert Decimal(ledger.consumed_qty) == Decimal(1)


async def test_marking_a_shipped_order_reviewed_clears_it_without_changing_anything(
    session, session_factory, connection, use_adapter
):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)

    await box_check.mark_reviewed(session, order)
    await session.commit()

    assert (await box_check.get_box_check_flags(session, [order]))[order.id] is False
    await session.refresh(small)
    assert Decimal(small.current_qty) == Decimal(9)
    assert (await get_kitting_cogs_by_order(session, [order.id]))[order.id] == Decimal("1.30")


async def test_mark_reviewed_is_refused_before_shipping(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)

    with pytest.raises(HTTPException) as exc:
        await box_check.mark_reviewed(session, order)
    assert exc.value.status_code == 400


async def test_preview_reports_stock_cost_and_profit_without_saving(session, session_factory, connection, use_adapter):
    order, small, large = await _shipped_with_both_boxes(session, use_adapter)

    preview = await box_check.preview_box_change(session, order, _confirm((large, "1")))

    assert preview.kitting_cost_before == Decimal("1.30")
    assert preview.kitting_cost_after == Decimal("0.90")
    assert preview.net_profit_delta == Decimal("0.40")
    [change] = preview.changes
    assert change.material_id == small.id and change.stock_effect == Decimal(1)
    await session.refresh(small)
    assert Decimal(small.current_qty) == Decimal(9)


async def test_cancelled_order_cannot_be_changed(session, session_factory, connection, use_adapter):
    order, small, large = await _two_box_order(session, use_adapter)
    order.status = OrderStatus.cancelled
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await box_check.confirm_boxes(session, order, _confirm((small, "1")))
    assert exc.value.status_code == 400
    assert (await box_check.get_box_check(session, order)).mode == "none"


async def test_single_unit_order_is_unaffected(session, session_factory, connection, use_adapter):
    boxes = await _category(session)
    box = await _packaging(session, "Box", boxes)
    await _product(session, "SKU-A", box)
    use_adapter([make_order("R-ONE", sku="SKU-A", qty=1)])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()

    assert await box_check.order_needs_box_check(session, order) is False
    summary = await box_check.get_box_check(session, order)
    assert summary.applies is False and summary.needs_check is False


# --- Through the endpoints ----------------------------------------------------------------------


async def test_endpoints_block_ship_then_confirm_then_ship(session, session_factory, connection, use_adapter):
    from app.routers import orders as orders_router

    order, small, large = await _two_box_order(session, use_adapter)
    order_id = order.id
    # Read before the rollback below expires everything this session holds.
    confirm_large = _confirm((large, "1"))

    read = await orders_router._serialize_one(session, await orders_router._get_order_with_lines(session, order_id))
    assert read.needs_box_check is True

    with pytest.raises(HTTPException) as exc:
        await orders_router.ship_order_endpoint(order_id, session=session)
    assert exc.value.status_code == 409
    await session.rollback()

    confirmed = await orders_router.confirm_order_box_check(order_id, confirm_large, session=session)
    assert confirmed.needs_box_check is False
    assert confirmed.box_check_confirmed_at is not None

    shipped = await orders_router.ship_order_endpoint(order_id, session=session)
    assert shipped.status == OrderStatus.shipped
    assert shipped.kitting_cogs == Decimal("0.90")


async def test_orders_list_flags_and_filters_by_box_check(session, session_factory, connection, use_adapter):
    from app.routers import orders as orders_router

    order, small, large = await _two_box_order(session, use_adapter)
    order_id = order.id

    page = await orders_router.list_orders(
        status_filter=None, q=None, needs_box_check=True, limit=50, offset=0, session=session
    )
    assert [o.id for o in page.items] == [order_id]
    assert page.total == 1 and page.items[0].needs_box_check is True

    await orders_router.confirm_order_box_check(order_id, _confirm((small, "1")), session=session)
    page = await orders_router.list_orders(
        status_filter=None, q=None, needs_box_check=True, limit=50, offset=0, session=session
    )
    assert page.total == 0


async def test_shipped_correction_through_the_endpoint_updates_profit(
    session, session_factory, connection, use_adapter
):
    from app.routers import orders as orders_router

    order, small, large = await _shipped_with_both_boxes(session, use_adapter)
    order_id = order.id
    before = await orders_router._serialize_one(session, await orders_router._get_order_with_lines(session, order_id))
    assert before.needs_box_check is True and before.kitting_cogs == Decimal("1.30")

    after = await orders_router.confirm_order_box_check(order_id, _confirm((large, "1")), session=session)

    assert after.needs_box_check is False
    assert after.kitting_cogs == Decimal("0.90")
    box = await orders_router.get_order_box_check(order_id, session=session)
    assert box.mode == "shipped" and [b.material_name for b in box.boxes if Decimal(b.qty) > 0] == ["Large Box"]


# --- Kitting flag chain on categories -----------------------------------------------------------


async def _chain_category(session, show: bool, auto: bool, check: bool) -> MaterialCategory:
    category = await material_categories.find_or_create(session, "Chain")
    category.show_in_kitting_bom_list = show
    category.auto_kitting_per_order = auto
    category.kitting_check_duplicates = check
    await session.commit()
    return category


def _patch(**fields) -> MaterialCategoryUpdate:
    return MaterialCategoryUpdate(name="Chain", **fields)


async def _save(session, category: MaterialCategory, payload: MaterialCategoryUpdate) -> MaterialCategory:
    from app.routers.material_categories import update_material_category

    return await update_material_category(category.id, payload, session=session)


async def test_turning_off_show_in_kitting_bom_list_switches_both_dependents_off(session):
    category = await _chain_category(session, True, True, True)

    saved = await _save(session, category, _patch(show_in_kitting_bom_list=False))

    assert (saved.show_in_kitting_bom_list, saved.auto_kitting_per_order, saved.kitting_check_duplicates) == (
        False,
        False,
        False,
    )


async def test_turning_off_one_per_order_switches_the_box_check_off(session):
    category = await _chain_category(session, True, True, True)

    saved = await _save(session, category, _patch(auto_kitting_per_order=False))

    assert saved.show_in_kitting_bom_list is True
    assert saved.auto_kitting_per_order is False and saved.kitting_check_duplicates is False


async def test_box_check_cannot_be_turned_on_without_one_per_order(session):
    category = await _chain_category(session, True, False, False)

    with pytest.raises(HTTPException) as exc:
        await _save(session, category, _patch(kitting_check_duplicates=True))
    assert exc.value.status_code == 400


async def test_one_per_order_cannot_be_turned_on_without_the_kitting_bom_list(session):
    category = await _chain_category(session, False, False, False)

    with pytest.raises(HTTPException) as exc:
        await _save(session, category, _patch(auto_kitting_per_order=True))
    assert exc.value.status_code == 400


async def test_turning_the_whole_chain_on_in_one_save_is_allowed(session):
    category = await _chain_category(session, False, False, False)

    saved = await _save(
        session,
        category,
        _patch(show_in_kitting_bom_list=True, auto_kitting_per_order=True, kitting_check_duplicates=True),
    )

    assert saved.kitting_check_duplicates is True


async def test_an_unrelated_edit_leaves_an_existing_inconsistent_category_alone(session):
    # Data that predates the rule: capped to one per order but not shown in the kitting BOM list.
    category = await _chain_category(session, False, True, False)

    saved = await _save(session, category, _patch(tracks_colour=True))

    assert saved.auto_kitting_per_order is True
