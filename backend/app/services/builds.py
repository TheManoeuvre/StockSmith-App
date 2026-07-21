from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build
from app.models.material import MaterialAdjustment
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant
from app.schemas.product import BomLine
from app.services.allocation import auto_allocate_after_build
from app.services.buildability import get_resolved_variant_bom
from app.services.costing import recompute_materials


async def create_build(
    session: AsyncSession, product_id: int, variant_id: int | None, qty_built: int, notes: str | None
) -> Build:
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

    build = Build(product_id=product_id, variant_id=variant_id, qty_built=qty_built, notes=notes)
    session.add(build)
    await session.flush()

    for line in bom:
        qty_delta = -(Decimal(qty_built) * line.qty_required)
        session.add(
            MaterialAdjustment(
                material_id=line.material_id,
                qty_delta=qty_delta,
                reason=f"Build #{build.id}",
                product_id=product_id,
                variant_id=variant_id,
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

    if variant is not None:
        variant.current_stock += qty_built
    else:
        product.current_stock += qty_built

    await auto_allocate_after_build(session, product_id, variant_id, source=f"build#{build.id}")

    await session.commit()
    await session.refresh(build)
    return build
