from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.kitting import ProductKittingMaterial, ProductVariantKittingMaterial
from app.models.material import Material
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.kitting import VariantKittingBomLine
from app.schemas.variant import VariantBomLine, VariantRead, VariantUpdate
from app.services import platform_fees
from app.services.buildability import compute_variant_buildability
from app.services.kitting import compute_max_sellable, sync_listing_ceiling_qty
from app.services.validation import validate_lines_against_units
from app.services.variants import compute_full_sku

router = APIRouter(prefix="/variants", tags=["variants"], dependencies=[Depends(require_auth)])


async def _validate_substitution_categories(
    session: AsyncSession, payload: list[VariantBomLine] | list[VariantKittingBomLine]
) -> None:
    """A substitution should only ever swap within the same material category (filament
    for filament, packaging for packaging, etc) — the frontend's substitute dropdown
    already filters to this, but a stale build or a direct API call could still submit a
    cross-category swap, so it's enforced here too."""
    material_ids = {l.material_id for l in payload} | {
        l.replaces_material_id for l in payload if l.replaces_material_id is not None
    }
    if not material_ids:
        return
    categories_by_id = dict(
        (await session.execute(select(Material.id, Material.category).where(Material.id.in_(material_ids)))).all()
    )
    for line in payload:
        if line.replaces_material_id is None:
            continue
        base_category = categories_by_id.get(line.replaces_material_id)
        sub_category = categories_by_id.get(line.material_id)
        if base_category is not None and sub_category is not None and base_category != sub_category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Material {line.material_id} ({sub_category.value}) cannot substitute material "
                    f"{line.replaces_material_id} ({base_category.value}) — categories must match"
                ),
            )


async def _to_variant_read(session: AsyncSession, variant: ProductVariant) -> VariantRead:
    product = await session.get(Product, variant.product_id)
    max_buildable, expected_max_buildable, cost_per_unit, effective_bom = await compute_variant_buildability(
        session, variant.product_id, variant.id
    )
    max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason, effective_kitting_bom = (
        await compute_max_sellable(
            session,
            variant.product_id,
            variant.id,
            variant.current_stock,
            variant.allocated_qty,
            expected_max_buildable,
            product.platform_ceiling_qty if product else None,
        )
    )
    full_sku = compute_full_sku(product.sku if product else None, variant.sku_suffix)
    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    return VariantRead.model_validate(variant).model_copy(
        update={
            "max_buildable": max_buildable,
            "expected_max_buildable": expected_max_buildable,
            "max_sellable": max_sellable,
            "max_sellable_reason": max_sellable_reason,
            "expected_max_sellable": expected_max_sellable,
            "expected_max_sellable_reason": expected_max_sellable_reason,
            "cost_per_unit": cost_per_unit,
            "effective_bom": effective_bom,
            "effective_kitting_bom": effective_kitting_bom,
            "full_sku": full_sku,
            "effective_platform_fee_percent": platform_fees.resolve_variant_fee_percent(
                fee_source, fee_components, variant, product
            ),
        }
    )


@router.get("/{variant_id}", response_model=VariantRead)
async def get_variant(variant_id: int, session: AsyncSession = Depends(get_db)) -> VariantRead:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
    read = await _to_variant_read(session, variant)
    await sync_listing_ceiling_qty(session, variant.product_id, variant.id, read.expected_max_sellable)
    await session.commit()
    return read


@router.patch("/{variant_id}", response_model=VariantRead)
async def update_variant(
    variant_id: int, payload: VariantUpdate, session: AsyncSession = Depends(get_db)
) -> VariantRead:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(variant, field, value)
    await session.commit()
    await session.refresh(variant)
    return await _to_variant_read(session, variant)


@router.delete("/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variant(variant_id: int, session: AsyncSession = Depends(get_db)) -> None:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
    variant.is_active = False
    await session.commit()


@router.put("/{variant_id}/bom-overrides", response_model=VariantRead)
async def replace_bom_overrides(
    variant_id: int, payload: list[VariantBomLine], session: AsyncSession = Depends(get_db)
) -> VariantRead:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")

    base_material_ids = set(
        (
            await session.execute(
                select(ProductMaterial.material_id).where(ProductMaterial.product_id == variant.product_id)
            )
        ).scalars()
    )

    substituted: set[int] = set()
    overridden_materials: set[int] = set()
    for line in payload:
        if line.material_id == line.replaces_material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Material {line.material_id} cannot substitute itself",
            )
        if line.replaces_material_id is not None:
            if line.replaces_material_id not in base_material_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is not part of the base BOM",
                )
            if line.replaces_material_id in substituted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is substituted more than once",
                )
            substituted.add(line.replaces_material_id)
        else:
            overridden_materials.add(line.material_id)

    conflicts = substituted & overridden_materials
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material {next(iter(conflicts))} has both an override and a substitution",
        )
    await _validate_substitution_categories(session, payload)

    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )

    await session.execute(delete(ProductVariantMaterial).where(ProductVariantMaterial.variant_id == variant_id))
    overrides = [
        ProductVariantMaterial(
            variant_id=variant_id,
            material_id=l.material_id,
            qty_required=l.qty_required,
            replaces_material_id=l.replaces_material_id,
        )
        for l in payload
    ]
    session.add_all(overrides)
    await session.commit()
    await session.refresh(variant)
    return await _to_variant_read(session, variant)


@router.put("/{variant_id}/kitting-bom-overrides", response_model=VariantRead)
async def replace_kitting_bom_overrides(
    variant_id: int, payload: list[VariantKittingBomLine], session: AsyncSession = Depends(get_db)
) -> VariantRead:
    variant = await session.get(ProductVariant, variant_id)
    if variant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")

    base_material_ids = set(
        (
            await session.execute(
                select(ProductKittingMaterial.material_id).where(
                    ProductKittingMaterial.product_id == variant.product_id
                )
            )
        ).scalars()
    )

    substituted: set[int] = set()
    overridden_materials: set[int] = set()
    for line in payload:
        if line.material_id == line.replaces_material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Material {line.material_id} cannot substitute itself",
            )
        if line.replaces_material_id is not None:
            if line.replaces_material_id not in base_material_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is not part of the base kitting BOM",
                )
            if line.replaces_material_id in substituted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is substituted more than once",
                )
            substituted.add(line.replaces_material_id)
        else:
            overridden_materials.add(line.material_id)

    conflicts = substituted & overridden_materials
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material {next(iter(conflicts))} has both an override and a substitution",
        )
    await _validate_substitution_categories(session, payload)

    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )

    await session.execute(
        delete(ProductVariantKittingMaterial).where(ProductVariantKittingMaterial.variant_id == variant_id)
    )
    overrides = [
        ProductVariantKittingMaterial(
            variant_id=variant_id,
            material_id=l.material_id,
            qty_required=l.qty_required,
            replaces_material_id=l.replaces_material_id,
        )
        for l in payload
    ]
    session.add_all(overrides)
    await session.commit()
    await session.refresh(variant)
    return await _to_variant_read(session, variant)
