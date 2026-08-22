"""Postage cost reaches an order however that order got allocated.

All three bugs here shared one trigger: an order the marketplace already reports as shipped
that couldn't be reconciled locally, because its line had no product mapping and therefore
nothing to allocate. That same unmapped line is why the order's shipping profile never
resolved — and so it shipped with no postage cost, reading as £0 in _compute_net_profit.

Driven end-to-end through order_sync.commit_sync, since that is the path a real Etsy/eBay
order takes and the reconciliation branch only exists there.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.kitting import OrderKittingAllocation, OrderKittingOverride, ProductKittingMaterial
from app.models.listing import ListingPlatform
from app.models.material import Material, MaterialUnit
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product, ProductMaterial
from app.models.shipping_profile import ShippingProfile
from app.routers.orders import (
    _postage_cost_missing,
    _serialize_one,
    allocate_order_endpoint,
    map_sku,
    ship_order_endpoint,
)
from app.schemas.order import MapSkuRequest
from app.services import builds, listing_push, material_categories, order_sync
from app.services.costing import recompute_material

from .conftest import make_order, received_purchase


@pytest.fixture(autouse=True)
def _async_material_push(monkeypatch, pushes):
    """conftest's `pushes` stubs enqueue_for_material with a plain lambda; reconcile_order_
    kitting awaits it. Same re-patch as test_kitting_multiunit_override."""

    async def _noop(session, material_id):
        return None

    monkeypatch.setattr(listing_push, "enqueue_for_material", _noop)


async def _stocked_material(session, category: str, name: str, unit: MaterialUnit, qty, total_cost) -> Material:
    row = await material_categories.find_or_create(session, category)
    material = Material(
        name=name,
        category=material_categories.legacy_value_for(row.name),
        category_id=row.id,
        unit=unit,
    )
    session.add(material)
    await session.flush()
    # A material only holds stock once a receipt exists — see conftest.received_purchase.
    await received_purchase(session, material.id, qty, Decimal(total_cost))
    await recompute_material(session, material.id)
    return material


def _profile(session, name="Small Parcel 48") -> ShippingProfile:
    profile = ShippingProfile(
        name=name,
        price=Decimal("3.60"),
        cost_etsy=Decimal("3.65"),
        cost_ebay=Decimal("3.20"),
        cost_manual=Decimal("3.65"),
    )
    session.add(profile)
    return profile


async def _buildable_product(session, sku: str, *, profile: ShippingProfile | None) -> Product:
    filament = await _stocked_material(session, "filament", "Filament", MaterialUnit.g, 10000, "200")
    product = Product(
        name=f"Product {sku}",
        sku=sku,
        current_stock=0,
        allocated_qty=0,
        shipping_profile_id=profile.id if profile else None,
    )
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=filament.id, qty_required=Decimal("10")))
    await session.commit()
    return product


async def test_mapping_an_unmapped_line_resolves_the_orders_shipping_profile(
    session, session_factory, connection, use_adapter, pushes
):
    """The reported bug. eBay/Etsy dispatched the order before StockSmith could allocate it,
    its SKU wasn't recognised, and the user mapped it by hand and shipped."""
    profile = _profile(session)
    await session.flush()
    product = await _buildable_product(session, "SKU-MAP", profile=profile)
    product.current_stock = 5
    await session.commit()

    use_adapter([make_order("R-MAP", sku="NOT-A-KNOWN-SKU", qty=1, is_shipped=True)])
    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    line = (await session.execute(select(OrderLine).where(OrderLine.order_id == order.id))).scalar_one()
    # The two symptoms of the one cause: nothing allocatable, and no profile resolvable.
    assert line.needs_mapping is True
    assert order.sync_issue is not None
    assert order.shipping_profile_id is None

    await map_sku(line.id, MapSkuRequest(product_id=product.id), session=session)
    await session.refresh(order)
    assert order.shipping_profile_id == profile.id

    read = await ship_order_endpoint(order.id, session=session)
    assert Decimal(read.shipping_cost_snapshot) == Decimal("3.65")
    assert read.postage_cost_missing is False


async def test_a_profile_arriving_after_the_order_shipped_still_gets_frozen(
    session, session_factory, connection, use_adapter, pushes
):
    """The product had no shipping profile when the order shipped; one is assigned later and
    a subsequent sync resolves it onto the order. Without the freeze here the order would
    show a profile name against a blank postage cost forever — update_order refuses shipping
    changes on a shipped order, so there is no way back."""
    product = await _buildable_product(session, "SKU-LATE", profile=None)
    product.current_stock = 5
    await session.commit()

    ext = make_order("R-LATE", sku="SKU-LATE", qty=1, is_shipped=True)
    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)

    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    assert order.status == OrderStatus.shipped
    assert order.shipping_profile_id is None
    assert order.shipping_cost_snapshot is None

    profile = _profile(session)
    await session.flush()
    product.shipping_profile_id = profile.id
    connection.last_orders_synced_at = None  # bring the receipt back into the fetch window
    await session.commit()

    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(order)

    assert order.shipping_profile_id == profile.id
    assert Decimal(order.shipping_cost_snapshot) == Decimal("3.65")


async def test_an_already_frozen_cost_is_never_refrozen(
    session, session_factory, connection, use_adapter, pushes
):
    """The post-ship freeze must only ever fill a gap. A profile whose cost changed after the
    order shipped must not rewrite what that order was actually charged."""
    profile = _profile(session)
    await session.flush()
    product = await _buildable_product(session, "SKU-FROZE", profile=profile)
    product.current_stock = 5
    await session.commit()

    ext = make_order("R-FROZE", sku="SKU-FROZE", qty=1, is_shipped=True)
    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    assert Decimal(order.shipping_cost_snapshot) == Decimal("3.65")

    profile.cost_etsy = Decimal("9.99")
    connection.last_orders_synced_at = None
    await session.commit()
    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)
    await session.refresh(order)

    assert Decimal(order.shipping_cost_snapshot) == Decimal("3.65")


async def test_a_manual_reassignment_survives_a_later_sync(
    session, session_factory, connection, use_adapter, pushes
):
    """The never-overwrite rule the original _default_shipping_profile_if_unset had, kept
    intact now that allocate_order calls it on every allocation rather than only at import."""
    default_profile = _profile(session)
    chosen = _profile(session, name="Large Letter 48")
    chosen.cost_etsy = Decimal("1.55")
    await session.flush()
    product = await _buildable_product(session, "SKU-KEEP", profile=default_profile)
    product.current_stock = 5
    await session.commit()

    ext = make_order("R-KEEP", sku="SKU-KEEP", qty=1)
    use_adapter([ext])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)

    order.shipping_profile_id = chosen.id
    await session.commit()
    await allocate_order_endpoint(order.id, session=session)
    await session.refresh(order)

    assert order.shipping_profile_id == chosen.id


async def test_build_allocated_multiunit_order_consumes_one_box_not_one_per_unit(
    session, session_factory, connection, use_adapter, pushes
):
    """The kitting half. The packaging BOM is configured only after the order was imported,
    so nothing applied the one-box-per-order default at import; stock then arrives via a
    build, whose allocation path is the one that used to skip it entirely."""
    profile = _profile(session)
    await session.flush()
    product = await _buildable_product(session, "SKU-BOX", profile=profile)

    use_adapter([make_order("R-BOX", sku="SKU-BOX", qty=3, is_shipped=True)])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()
    await session.refresh(order)
    assert order.sync_issue is not None  # nothing in stock to reconcile against yet

    box = await _stocked_material(session, "packaging", "Box", MaterialUnit.each, 100, "50")
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=Decimal("1")))
    await session.commit()

    await builds.create_build(session, product_id=product.id, variant_id=None, qty_built=3, notes=None)
    await session.commit()

    override = (
        await session.execute(select(OrderKittingOverride).where(OrderKittingOverride.order_id == order.id))
    ).scalar_one()
    assert Decimal(override.qty_required) == Decimal(1)

    read = await ship_order_endpoint(order.id, session=session)
    ledger = (
        await session.execute(select(OrderKittingAllocation).where(OrderKittingAllocation.order_id == order.id))
    ).scalar_one()
    # One parcel, one box — not three, which is what shipped for real orders 72 and 87.
    assert Decimal(ledger.consumed_qty) == Decimal(1)
    assert Decimal(read.kitting_cogs) == Decimal("0.50")


async def test_postage_cost_missing_only_flags_a_shipped_order(session, session_factory, connection, use_adapter, pushes):
    """shipping_cost_snapshot is legitimately NULL until ship_order freezes it, so the badge
    must stay off until the units have actually left the building."""
    product = await _buildable_product(session, "SKU-FLAG", profile=None)
    product.current_stock = 5
    await session.commit()

    use_adapter([make_order("R-FLAG", sku="SKU-FLAG", qty=1)])
    await order_sync.commit_sync(ListingPlatform.etsy)
    order = (await session.execute(select(Order))).scalar_one()

    read = await _serialize_one(session, await _order_with_lines(session, order.id))
    assert read.status == OrderStatus.allocated
    assert read.postage_cost_missing is False

    shipped = await ship_order_endpoint(order.id, session=session)
    assert shipped.status == OrderStatus.shipped
    assert shipped.shipping_cost_snapshot is None
    assert shipped.postage_cost_missing is True
    # And it does not pretend the goods were free either — net profit simply omits postage.
    assert _postage_cost_missing(await _order_with_lines(session, order.id)) is True


async def _order_with_lines(session, order_id: int) -> Order:
    from app.routers.orders import _get_order_with_lines

    return await _get_order_with_lines(session, order_id)
