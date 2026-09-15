"""Material-level substitution fallbacks (app.models.material_substitute).

Covers: creating/ranking substitutes, cross-category substitution being allowed,
self-substitution being rejected, the two shortage points (build/buildability.py,
kitting/services/kitting.py) surfacing ranked suggestions, and the audit log
recording a chosen substitute.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.build import Build
from app.models.kitting import OrderKittingAllocation, ProductKittingMaterial
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.material_substitute import MaterialSubstitute, MaterialSubstituteUsage
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant
from app.routers.material_substitutes import (
    add_material_substitute,
    create_material_substitute_usage,
    list_material_substitutes,
    update_material_substitute,
)
from app.schemas.material_substitute import (
    MaterialSubstituteCreate,
    MaterialSubstituteUpdate,
    MaterialSubstituteUsageCreate,
)
from app.services.buildability import compute_variant_buildability, get_buildable_by_product
from app.services.kitting import (
    compute_variant_kitting_capacity,
    compute_variants_kitting_capacity_bulk,
    get_expected_kitting_capacity_by_product,
    get_kitting_capacity_by_product,
    get_orders_awaiting_packaging,
)
from app.services.material_substitutes import get_ranked_substitutes, record_substitute_usage


async def _material(session, name: str, *, category=LegacyMaterialCategory.filament, unit=MaterialUnit.g, qty=Decimal(0), category_name: str | None = None) -> Material:
    category_id = None
    if category_name is not None:
        row = (
            await session.execute(
                MaterialCategory.__table__.select().where(MaterialCategory.name == category_name)
            )
        ).first()
        category_id = row.id if row else None
    m = Material(name=name, category=category, category_id=category_id, unit=unit, current_qty=qty)
    session.add(m)
    await session.flush()
    return m


# ---------------------------------------------------------------------------
# Creating / ranking substitutes
# ---------------------------------------------------------------------------


async def test_add_and_list_substitutes_ranked(session):
    pla = await _material(session, "PLA Red")
    pla_alt = await _material(session, "PLA Blue")
    pla_alt2 = await _material(session, "PLA Green")
    await session.commit()

    await add_material_substitute(
        pla.id, MaterialSubstituteCreate(substitute_material_id=pla_alt2.id, rank=5, notes="second choice"), session
    )
    await add_material_substitute(
        pla.id, MaterialSubstituteCreate(substitute_material_id=pla_alt.id, rank=1, notes="closest match"), session
    )

    result = await list_material_substitutes(pla.id, session)
    assert [r.substitute_material_id for r in result] == [pla_alt.id, pla_alt2.id]
    assert result[0].notes == "closest match"
    assert result[0].substitute_material_name == "PLA Blue"


async def test_reorder_via_rank_update_changes_list_order(session):
    pla = await _material(session, "PLA Red")
    first = await _material(session, "PLA Blue")
    second = await _material(session, "PLA Green")
    await session.commit()

    a = await add_material_substitute(pla.id, MaterialSubstituteCreate(substitute_material_id=first.id, rank=0), session)
    await add_material_substitute(pla.id, MaterialSubstituteCreate(substitute_material_id=second.id, rank=1), session)

    await update_material_substitute(pla.id, a.id, MaterialSubstituteUpdate(rank=10), session)

    result = await list_material_substitutes(pla.id, session)
    assert [r.substitute_material_id for r in result] == [second.id, first.id]


async def test_deactivate_removes_substitute_from_ranked_suggestions(session):
    pla = await _material(session, "PLA Red")
    alt = await _material(session, "PLA Blue")
    await session.commit()

    sub = await add_material_substitute(pla.id, MaterialSubstituteCreate(substitute_material_id=alt.id), session)
    assert len(await get_ranked_substitutes(session, pla.id)) == 1

    await update_material_substitute(pla.id, sub.id, MaterialSubstituteUpdate(is_active=False), session)

    assert await get_ranked_substitutes(session, pla.id) == []
    # Still listed (history preserved), just flagged inactive.
    listed = await list_material_substitutes(pla.id, session)
    assert listed[0].is_active is False


# ---------------------------------------------------------------------------
# Cross-category substitution allowed / self-substitution rejected
# ---------------------------------------------------------------------------


async def test_cross_category_substitution_is_allowed(session):
    filament = await _material(session, "PLA", category=LegacyMaterialCategory.filament, category_name="filament")
    packaging = await _material(
        session, "Spare Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, category_name="packaging"
    )
    await session.commit()

    # No category equality check here (unlike routers.variants._validate_substitution_categories)
    # — this should succeed even though the two materials are in different categories.
    result = await add_material_substitute(
        filament.id, MaterialSubstituteCreate(substitute_material_id=packaging.id, notes="emergency stand-in"), session
    )
    assert result.substitute_material_id == packaging.id


async def test_self_substitution_is_rejected_by_the_router(session):
    m = await _material(session, "PLA Red")
    await session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await add_material_substitute(m.id, MaterialSubstituteCreate(substitute_material_id=m.id), session)
    assert exc_info.value.status_code == 400


async def test_self_substitution_is_rejected_at_the_db_level(session):
    m = await _material(session, "PLA Red")
    await session.commit()

    session.add(MaterialSubstitute(material_id=m.id, substitute_material_id=m.id))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_duplicate_pair_is_rejected(session):
    a = await _material(session, "PLA Red")
    b = await _material(session, "PLA Blue")
    await session.commit()

    await add_material_substitute(a.id, MaterialSubstituteCreate(substitute_material_id=b.id), session)

    with pytest.raises(HTTPException) as exc_info:
        await add_material_substitute(a.id, MaterialSubstituteCreate(substitute_material_id=b.id), session)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Shortage endpoints surfacing ranked suggestions
# ---------------------------------------------------------------------------


async def test_build_shortfall_surfaces_ranked_substitutes(session):
    short = await _material(session, "PLA Red", qty=Decimal(0))
    fallback_best = await _material(session, "PLA Blue", qty=Decimal(500))
    fallback_worse = await _material(session, "PLA Green", qty=Decimal(500))
    await session.commit()

    await add_material_substitute(
        short.id, MaterialSubstituteCreate(substitute_material_id=fallback_worse.id, rank=5), session
    )
    await add_material_substitute(
        short.id, MaterialSubstituteCreate(substitute_material_id=fallback_best.id, rank=1, notes="best match"), session
    )

    product = Product(name="Keyring", sku="K-1")
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=short.id, qty_required=Decimal(10)))
    variant = ProductVariant(product_id=product.id, variant_name="Default")
    session.add(variant)
    await session.commit()

    figures, _, bom = await compute_variant_buildability(session, product.id, variant.id)
    assert figures.max_buildable == 0
    # 500 from each fallback at 10 per unit — the fallback pool is what the sellable
    # figures build on, while max_buildable stays the material-only 0 so the shortfall
    # (and its suggestions) stay visible.
    assert figures.max_buildable_incl_fallbacks == 100

    line = next(l for l in bom if l.material_id == short.id)
    assert [s.material_id for s in line.suggested_substitutes] == [fallback_best.id, fallback_worse.id]
    assert line.suggested_substitutes[0].available_qty == Decimal(500)
    assert line.suggested_substitutes[0].notes == "best match"


async def test_build_line_with_enough_stock_gets_no_suggestions(session):
    plentiful = await _material(session, "PLA Red", qty=Decimal(1000))
    fallback = await _material(session, "PLA Blue", qty=Decimal(500))
    await session.commit()
    await add_material_substitute(plentiful.id, MaterialSubstituteCreate(substitute_material_id=fallback.id), session)

    product = Product(name="Keyring", sku="K-1")
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=plentiful.id, qty_required=Decimal(10)))
    variant = ProductVariant(product_id=product.id, variant_name="Default")
    session.add(variant)
    await session.commit()

    _, _, bom = await compute_variant_buildability(session, product.id, variant.id)
    line = next(l for l in bom if l.material_id == plentiful.id)
    assert line.suggested_substitutes == []


async def test_kitting_packaging_shortfall_surfaces_ranked_substitutes(session):
    box = await _material(session, "Small Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, qty=Decimal(0))
    bigger_box = await _material(
        session, "Medium Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, qty=Decimal(20)
    )
    await session.commit()
    await add_material_substitute(
        box.id, MaterialSubstituteCreate(substitute_material_id=bigger_box.id, rank=0, notes="fits, just bigger"), session
    )

    product = Product(name="Widget", sku="W-1")
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=Decimal(1)))
    order = Order(status=OrderStatus.allocated, order_placed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    session.add(order)
    await session.flush()
    session.add(
        OrderLine(order_id=order.id, product_id=product.id, ordered_qty=1, allocated_qty=1, shipped_qty=0)
    )
    await session.commit()

    awaiting = await get_orders_awaiting_packaging(session)
    assert len(awaiting) == 1
    entry = awaiting[0]
    assert entry.material_id == box.id
    assert len(entry.suggested_substitutes) == 1
    assert entry.suggested_substitutes[0].material_id == bigger_box.id
    assert entry.suggested_substitutes[0].notes == "fits, just bigger"


# ---------------------------------------------------------------------------
# Packaging capacity pools fallbacks
# ---------------------------------------------------------------------------


async def _packaging_product_with_fallback(session, *, box_qty, fallback_qty):
    """A product whose only kitting line is `box` (1 per unit), where `box` lists
    `bigger_box` as its rank-0 fallback. Returns (product, variant, box, bigger_box)."""
    box = await _material(session, "Small Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, qty=box_qty)
    bigger_box = await _material(
        session, "Medium Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, qty=fallback_qty
    )
    await session.commit()
    await add_material_substitute(
        box.id, MaterialSubstituteCreate(substitute_material_id=bigger_box.id, rank=0), session
    )
    product = Product(name="Widget", sku="W-1", current_stock=50)
    session.add(product)
    await session.flush()
    session.add(ProductKittingMaterial(product_id=product.id, material_id=box.id, qty_required=Decimal(1)))
    variant = ProductVariant(product_id=product.id, variant_name="Default", current_stock=50)
    session.add(variant)
    await session.commit()
    return product, variant, box, bigger_box


async def test_kitting_capacity_counts_active_fallback_stock_on_every_path(session):
    """3 small boxes + 20 medium (the fallback) = 23 packable, not 3 — and the product-list
    SQL, the per-variant path and the bulk per-variant path all say the same thing."""
    product, variant, box, bigger_box = await _packaging_product_with_fallback(
        session, box_qty=Decimal(3), fallback_qty=Decimal(20)
    )

    assert (await get_kitting_capacity_by_product(session))[product.id] == 23
    assert (await get_expected_kitting_capacity_by_product(session))[product.id] == 23

    capacity, expected_capacity, bom = await compute_variant_kitting_capacity(session, product.id, variant.id)
    assert (capacity, expected_capacity) == (23, 23)
    line = next(l for l in bom if l.material_id == box.id)
    assert line.line_max_buildable == 3
    assert line.line_max_buildable_incl_fallbacks == 23

    bulk = await compute_variants_kitting_capacity_bulk(session, product.id, [variant.id])
    assert bulk[variant.id][:2] == (23, 23)


async def test_kitting_capacity_ignores_deactivated_fallback_and_its_allocated_stock(session):
    product, variant, box, bigger_box = await _packaging_product_with_fallback(
        session, box_qty=Decimal(3), fallback_qty=Decimal(20)
    )
    # Reserved fallback stock isn't free to lend.
    bigger_box.allocated_qty = Decimal(15)
    await session.commit()
    assert (await get_kitting_capacity_by_product(session))[product.id] == 8
    capacity, _, _ = await compute_variant_kitting_capacity(session, product.id, variant.id)
    assert capacity == 8

    # A deactivated fallback drops out of the pool entirely.
    sub = (await list_material_substitutes(box.id, session))[0]
    await update_material_substitute(box.id, sub.id, MaterialSubstituteUpdate(is_active=False), session)
    assert (await get_kitting_capacity_by_product(session))[product.id] == 3
    capacity, _, bom = await compute_variant_kitting_capacity(session, product.id, variant.id)
    assert capacity == 3
    assert next(l for l in bom if l.material_id == box.id).line_max_buildable_incl_fallbacks == 3


async def test_kitting_line_short_on_its_own_shelf_still_suggests_even_when_fallback_covers(session):
    """The whole point of keeping line_max_buildable material-only: a box with none on the
    shelf is a shortfall someone has to resolve at pack time, so its ranked fallbacks are
    offered on the kitting line — even though the fallback is already carrying capacity."""
    product, variant, box, bigger_box = await _packaging_product_with_fallback(
        session, box_qty=Decimal(0), fallback_qty=Decimal(20)
    )
    capacity, _, bom = await compute_variant_kitting_capacity(session, product.id, variant.id)
    assert capacity == 20
    line = next(l for l in bom if l.material_id == box.id)
    assert line.line_max_buildable == 0
    assert [s.material_id for s in line.suggested_substitutes] == [bigger_box.id]

    bulk = await compute_variants_kitting_capacity_bulk(session, product.id, [variant.id])
    bulk_line = next(l for l in bulk[variant.id][2] if l.material_id == box.id)
    assert [s.material_id for s in bulk_line.suggested_substitutes] == [bigger_box.id]


async def test_build_capacity_reports_material_only_and_fallback_pooled_side_by_side(session):
    """10 via the BOM as written, 20 counting fallbacks — both figures, on the product-list
    aggregate and the per-variant path alike, so the UI can show the split."""
    pla = await _material(session, "PLA Red", qty=Decimal(100))
    pla_alt = await _material(session, "PLA Blue", qty=Decimal(100))
    await session.commit()
    await add_material_substitute(pla.id, MaterialSubstituteCreate(substitute_material_id=pla_alt.id), session)

    product = Product(name="Keyring", sku="K-2")
    session.add(product)
    await session.flush()
    session.add(ProductMaterial(product_id=product.id, material_id=pla.id, qty_required=Decimal(10)))
    variant = ProductVariant(product_id=product.id, variant_name="Default")
    session.add(variant)
    await session.commit()

    by_product = (await get_buildable_by_product(session))[product.id]
    assert (by_product.max_buildable, by_product.max_buildable_incl_fallbacks) == (10, 20)
    assert (by_product.expected_max_buildable, by_product.expected_max_buildable_incl_fallbacks) == (10, 20)

    figures, _, bom = await compute_variant_buildability(session, product.id, variant.id)
    assert (figures.max_buildable, figures.max_buildable_incl_fallbacks) == (10, 20)
    line = next(l for l in bom if l.material_id == pla.id)
    assert (line.line_max_buildable, line.line_max_buildable_incl_fallbacks) == (10, 20)
    # Not short on its own shelf, so nothing to suggest.
    assert line.suggested_substitutes == []


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_recording_substitute_usage_creates_an_audit_row(session):
    short = await _material(session, "PLA Red")
    fallback = await _material(session, "PLA Blue", qty=Decimal(500))
    await session.commit()

    order = Order(status=OrderStatus.pending, order_placed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    session.add(order)
    await session.commit()

    usage = await record_substitute_usage(
        session,
        material_id=short.id,
        substitute_material_id=fallback.id,
        qty=Decimal(25),
        order_id=order.id,
        build_id=None,
        notes="used blue instead of red, out of stock",
        created_by="shop-tablet",
    )

    assert usage.id is not None
    fetched = await session.get(MaterialSubstituteUsage, usage.id)
    assert fetched.material_id == short.id
    assert fetched.substitute_material_id == fallback.id
    assert fetched.qty == Decimal(25)
    assert fetched.order_id == order.id
    assert fetched.created_by == "shop-tablet"


async def test_usage_endpoint_records_and_validates_references(session):
    short = await _material(session, "PLA Red")
    fallback = await _material(session, "PLA Blue", qty=Decimal(500))
    await session.commit()

    build = Build(product_id=(await _seed_product(session)).id, qty_built=1)
    session.add(build)
    await session.commit()

    result = await create_material_substitute_usage(
        MaterialSubstituteUsageCreate(
            material_id=short.id,
            substitute_material_id=fallback.id,
            qty=Decimal(3),
            build_id=build.id,
            notes="build ran short",
            created_by="floor-tablet",
        ),
        session,
    )
    assert result.build_id == build.id
    assert result.created_by == "floor-tablet"

    with pytest.raises(HTTPException) as exc_info:
        await create_material_substitute_usage(
            MaterialSubstituteUsageCreate(material_id=short.id, substitute_material_id=999999),
            session,
        )
    assert exc_info.value.status_code == 404


async def _seed_product(session) -> Product:
    product = Product(name="Widget", sku=f"W-{id(session)}")
    session.add(product)
    await session.flush()
    return product
