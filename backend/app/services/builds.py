from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build, BuildFailedConsumption
from app.models.material import Material, MaterialAdjustment, MaterialCategory
from app.models.product import Product, ProductMaterial
from app.models.product_stock_event import ProductStockEventType
from app.models.variant import ProductVariant
from app.schemas.product import BomLine
from app.services import listing_push
from app.services.allocation import auto_allocate_after_build
from app.services.buildability import get_resolved_variant_bom
from app.services.costing import recompute_materials
from app.services.stock_events import record_stock_event


async def create_build(
    session: AsyncSession,
    product_id: int,
    variant_id: int | None,
    qty_built: int,
    notes: str | None,
    qty_failed: int = 0,
    failed_consumption: dict[int, bool] | None = None,
) -> Build:
    """Records a build event: qty_built units successfully produced, plus qty_failed
    units attempted but not (e.g. a print that failed partway).

    failed_consumption maps material_id -> whether that BOM line was actually consumed
    for the failed qty (a failed print may not have burned through the whole BOM). When
    not given and qty_failed > 0, defaults to "filament consumed, everything else not" —
    the common case for a 3D-print failure, still overridable by passing an explicit map.
    Ignored entirely when qty_failed is 0.
    """
    if qty_built < 0 or qty_failed < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="qty_built/qty_failed cannot be negative")
    if qty_built == 0 and qty_failed == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Must record at least one built or failed unit")

    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    # Only *active* variants count — a product whose variants have all been disabled is
    # treated the same as a product with no variants at all: build against the base
    # product's own SKU/BOM/stock rather than forcing a (disabled) variant to be picked.
    has_active_variants = (
        await session.execute(
            select(ProductVariant.id).where(
                ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)
            ).limit(1)
        )
    ).scalar_one_or_none() is not None

    if has_active_variants and variant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This product has variants — specify a variant_id"
        )
    if not has_active_variants and variant_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This product has no active variants — omit variant_id"
        )

    variant: ProductVariant | None = None
    if variant_id is not None:
        variant = await session.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
        if not variant.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot build a disabled variant")
        bom = await get_resolved_variant_bom(session, product_id, variant_id)
    else:
        result = await session.execute(select(ProductMaterial).where(ProductMaterial.product_id == product_id))
        bom = [
            BomLine(material_id=line.material_id, qty_required=Decimal(line.qty_required))
            for line in result.scalars()
        ]

    if not bom:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Product has no BOM defined")

    materials_by_id = {
        m.id: m
        for m in (
            await session.execute(select(Material).where(Material.id.in_([line.material_id for line in bom])))
        ).scalars()
    }

    if qty_failed > 0 and failed_consumption is None:
        failed_consumption = {
            line.material_id: materials_by_id[line.material_id].category == MaterialCategory.filament for line in bom
        }

    build = Build(product_id=product_id, variant_id=variant_id, qty_built=qty_built, qty_failed=qty_failed, notes=notes)
    session.add(build)
    await session.flush()

    for line in bom:
        qty_delta = -(Decimal(qty_built) * line.qty_required)
        if qty_delta != 0:
            session.add(
                MaterialAdjustment(
                    material_id=line.material_id,
                    qty_delta=qty_delta,
                    reason=f"Build #{build.id}",
                    product_id=product_id,
                    variant_id=variant_id,
                )
            )

    if qty_failed > 0:
        for line in bom:
            was_consumed = failed_consumption.get(line.material_id, False) if failed_consumption else False
            material = materials_by_id[line.material_id]
            qty_consumed = Decimal(qty_failed) * line.qty_required if was_consumed else Decimal(0)
            if was_consumed and qty_consumed != 0:
                session.add(
                    MaterialAdjustment(
                        material_id=line.material_id,
                        qty_delta=-qty_consumed,
                        reason=f"Build #{build.id} (failed units)",
                        product_id=product_id,
                        variant_id=variant_id,
                    )
                )
            session.add(
                BuildFailedConsumption(
                    build_id=build.id,
                    material_id=line.material_id,
                    was_consumed=was_consumed,
                    qty_consumed=qty_consumed,
                    unit_cost_snapshot=Decimal(material.avg_unit_cost),
                )
            )

    material_ids = {line.material_id for line in bom}
    try:
        await recompute_materials(session, material_ids)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient material stock for this build"
        )

    owner = variant if variant is not None else product
    if qty_built > 0:
        owner.current_stock += qty_built
        listing_push.enqueue_for_owner(owner)
        record_stock_event(
            session,
            product_id=product_id,
            variant_id=variant_id,
            event_type=ProductStockEventType.build_success,
            qty_delta=qty_built,
            running_balance=owner.current_stock,
            reason=notes,
            source_build_id=build.id,
        )

    if qty_failed > 0:
        # A failed build never touches current_stock — material was consumed but no
        # usable unit resulted — so qty_delta is 0 and running_balance is unchanged.
        # Still gets its own row so the failure (and the material burned on it) shows up
        # in the same timeline as everything else touching this product.
        record_stock_event(
            session,
            product_id=product_id,
            variant_id=variant_id,
            event_type=ProductStockEventType.build_failed,
            qty_delta=0,
            running_balance=owner.current_stock,
            reason=notes,
            source_build_id=build.id,
        )

    if qty_built > 0:
        await auto_allocate_after_build(session, product_id, variant_id, source=f"build#{build.id}")

    await session.commit()
    await session.refresh(build)
    return build
