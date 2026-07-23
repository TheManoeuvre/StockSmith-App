from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.build import Build
from app.models.kitting import ProductKittingMaterial
from app.models.product import Product, ProductBundleItem, ProductMaterial
from app.models.stock_adjustment import StockAdjustment
from app.models.variant import ProductVariant
from app.schemas.build import BuildRead
from app.schemas.kitting import KittingBomLine, KittingBomLineRead
from app.schemas.stock_adjustment import StockAdjustmentRead
from app.models.pricing import ProductPriceSnapshot
from app.schemas.product import (
    BomLine,
    BomLineRead,
    BundleItem,
    BundleItemRead,
    GenerateVariantsRequest,
    ProductCreate,
    ProductPriceSnapshotRead,
    ProductRead,
    ProductUpdate,
)
from app.schemas.variant import VariantCreate, VariantRead
from app.services.buildability import (
    compute_variant_buildability,
    compute_variants_buildability_bulk,
    get_active_variant_stock_totals_by_product,
    get_bundle_cost_per_unit,
    get_cost_per_unit_by_product,
    get_expected_max_buildable_by_product,
    get_max_buildable_by_product,
    get_ready_to_ship_by_bundle,
)
from app.services import platform_fees
from app.services.csv_io import export_products_csv, import_products_csv
from app.services.kitting import (
    apply_platform_ceiling,
    combine_expected_max_sellable,
    combine_max_sellable,
    compute_max_sellable,
    compute_max_sellable_bulk,
    get_expected_kitting_capacity_by_product,
    get_kitting_capacity_by_product,
    sync_listing_ceiling_qty,
)
from app.services.pricing import snapshot_product_pricing
from app.services.shipping_profiles import (
    get_shipping_profiles_by_id,
    resolve_product_shipping_profile,
    resolve_variant_shipping_profile,
)
from app.services.validation import validate_lines_against_units
from app.services.variants import compute_full_sku, generate_variants

router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(require_auth)])

_MAIN_IMAGE_ASSET_ID_BY_PRODUCT_SQL = text(
    """
    SELECT DISTINCT ON (product_id) id, product_id
    FROM product_assets
    WHERE asset_type = 'main_image'
    ORDER BY product_id, display_order
    """
)


async def _get_main_image_asset_id_by_product(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(_MAIN_IMAGE_ASSET_ID_BY_PRODUCT_SQL)
    return {row.product_id: row.id for row in result}


def _read_product(
    product: Product,
    max_buildable_by_product: dict,
    expected_max_buildable_by_product: dict,
    cost_per_unit_by_product: dict,
    main_image_asset_id_by_product: dict,
    ready_to_ship_by_bundle: dict,
    bundle_cost_per_unit: dict,
    kitting_capacity_by_product: dict,
    expected_kitting_capacity_by_product: dict,
    active_variant_stock_totals_by_product: dict,
    fee_source,
    fee_components,
    shipping_profiles_by_id: dict,
) -> ProductRead:
    current_stock = product.current_stock
    allocated_qty = product.allocated_qty
    if product.is_bundle:
        max_buildable = None
        expected_max_buildable = None
        cost_per_unit = bundle_cost_per_unit.get(product.id)
        ready_to_ship = ready_to_ship_by_bundle.get(product.id)
        max_sellable = None
        max_sellable_reason = None
        expected_max_sellable = None
        expected_max_sellable_reason = None
    else:
        # A product with active variants never accumulates its own current_stock/
        # allocated_qty (builds always target the variant row) — use the summed variant
        # totals here instead, so this matches what the product detail page already
        # computes client-side, and so max_sellable's free_stock input is correct too.
        current_stock, allocated_qty = active_variant_stock_totals_by_product.get(
            product.id, (product.current_stock, product.allocated_qty)
        )
        max_buildable = max_buildable_by_product.get(product.id)
        expected_max_buildable = expected_max_buildable_by_product.get(product.id)
        cost_per_unit = cost_per_unit_by_product.get(product.id)
        ready_to_ship = None
        max_sellable, max_sellable_reason = combine_max_sellable(
            current_stock - allocated_qty, kitting_capacity_by_product.get(product.id)
        )
        expected_max_sellable, expected_max_sellable_reason = combine_expected_max_sellable(
            expected_max_buildable, expected_kitting_capacity_by_product.get(product.id)
        )
        max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason = apply_platform_ceiling(
            max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason,
            product.platform_ceiling_qty,
        )
    shipping_profile = resolve_product_shipping_profile(shipping_profiles_by_id, product)
    effective_platform_fee_percent = platform_fees.resolve_fee_percent(
        fee_source,
        fee_components,
        product.platform_fee_percent,
        product.sale_price,
        shipping_profile.price if shipping_profile else None,
    )
    return ProductRead.model_validate(product).model_copy(
        update={
            "current_stock": current_stock,
            "allocated_qty": allocated_qty,
            "max_buildable": max_buildable,
            "expected_max_buildable": expected_max_buildable,
            "max_sellable": max_sellable,
            "max_sellable_reason": max_sellable_reason,
            "expected_max_sellable": expected_max_sellable,
            "expected_max_sellable_reason": expected_max_sellable_reason,
            "cost_per_unit": cost_per_unit,
            "main_image_asset_id": main_image_asset_id_by_product.get(product.id),
            "ready_to_ship": ready_to_ship,
            "effective_platform_fee_percent": effective_platform_fee_percent,
        }
    )


@router.get("", response_model=list[ProductRead])
async def list_products(session: AsyncSession = Depends(get_db)) -> list[ProductRead]:
    result = await session.execute(select(Product).order_by(Product.name))
    products = list(result.scalars())
    max_buildable_by_product = await get_max_buildable_by_product(session)
    expected_max_buildable_by_product = await get_expected_max_buildable_by_product(session)
    cost_per_unit_by_product = await get_cost_per_unit_by_product(session)
    main_image_asset_id_by_product = await _get_main_image_asset_id_by_product(session)
    ready_to_ship_by_bundle = await get_ready_to_ship_by_bundle(session)
    bundle_cost_per_unit = await get_bundle_cost_per_unit(session, cost_per_unit_by_product)
    kitting_capacity_by_product = await get_kitting_capacity_by_product(session)
    expected_kitting_capacity_by_product = await get_expected_kitting_capacity_by_product(session)
    active_variant_stock_totals_by_product = await get_active_variant_stock_totals_by_product(session)
    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    return [
        _read_product(
            p,
            max_buildable_by_product,
            expected_max_buildable_by_product,
            cost_per_unit_by_product,
            main_image_asset_id_by_product,
            ready_to_ship_by_bundle,
            bundle_cost_per_unit,
            kitting_capacity_by_product,
            expected_kitting_capacity_by_product,
            active_variant_stock_totals_by_product,
            fee_source,
            fee_components,
            shipping_profiles_by_id,
        )
        for p in products
    ]


@router.get("/export")
async def export_products(session: AsyncSession = Depends(get_db)) -> Response:
    csv_text = await export_products_csv(session)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products.csv"},
    )


@router.post("/import")
async def import_products(file: UploadFile, session: AsyncSession = Depends(get_db)) -> dict:
    content = await file.read()
    return await import_products_csv(session, content)


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate, session: AsyncSession = Depends(get_db)) -> Product:
    data = payload.model_dump()
    sku = data.pop("sku", None)
    product = Product(**data)
    session.add(product)
    await session.flush()  # assigns product.id without a second commit round-trip
    product.sku = sku or f"SKU-{product.id:04d}"
    await session.commit()
    await session.refresh(product)
    return product


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, session: AsyncSession = Depends(get_db)) -> ProductRead:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    max_buildable_by_product = await get_max_buildable_by_product(session)
    expected_max_buildable_by_product = await get_expected_max_buildable_by_product(session)
    cost_per_unit_by_product = await get_cost_per_unit_by_product(session)
    main_image_asset_id_by_product = await _get_main_image_asset_id_by_product(session)
    ready_to_ship_by_bundle = await get_ready_to_ship_by_bundle(session)
    bundle_cost_per_unit = await get_bundle_cost_per_unit(session, cost_per_unit_by_product)
    kitting_capacity_by_product = await get_kitting_capacity_by_product(session)
    expected_kitting_capacity_by_product = await get_expected_kitting_capacity_by_product(session)
    active_variant_stock_totals_by_product = await get_active_variant_stock_totals_by_product(session)
    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    read = _read_product(
        product,
        max_buildable_by_product,
        expected_max_buildable_by_product,
        cost_per_unit_by_product,
        main_image_asset_id_by_product,
        ready_to_ship_by_bundle,
        bundle_cost_per_unit,
        kitting_capacity_by_product,
        expected_kitting_capacity_by_product,
        active_variant_stock_totals_by_product,
        fee_source,
        fee_components,
        shipping_profiles_by_id,
    )
    await sync_listing_ceiling_qty(session, product_id, None, read.expected_max_sellable)
    await session.commit()
    return read


_PRICING_FIELDS = {"sale_price", "shipping_profile_id", "platform_fee_percent"}


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(product_id: int, payload: ProductUpdate, session: AsyncSession = Depends(get_db)) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    changed_fields = set(payload.model_dump(exclude_unset=True).keys())
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)

    if changed_fields & _PRICING_FIELDS:
        cost_per_unit_by_product = await get_cost_per_unit_by_product(session)
        cost_per_unit = (
            await get_bundle_cost_per_unit(session, cost_per_unit_by_product)
            if product.is_bundle
            else cost_per_unit_by_product
        ).get(product_id)
        if cost_per_unit is not None:
            await snapshot_product_pricing(session, product, cost_per_unit)

    await session.commit()
    await session.refresh(product)
    return product


@router.get("/{product_id}/price-history", response_model=list[ProductPriceSnapshotRead])
async def get_price_history(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductPriceSnapshot]:
    result = await session.execute(
        select(ProductPriceSnapshot)
        .where(ProductPriceSnapshot.product_id == product_id)
        .order_by(ProductPriceSnapshot.recorded_at.desc())
    )
    return list(result.scalars())


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: int, session: AsyncSession = Depends(get_db)) -> None:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    product.is_active = False
    await session.commit()


@router.get("/{product_id}/bom", response_model=list[BomLineRead])
async def get_bom(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductMaterial]:
    result = await session.execute(select(ProductMaterial).where(ProductMaterial.product_id == product_id))
    return list(result.scalars())


@router.put("/{product_id}/bom", response_model=list[BomLineRead])
async def replace_bom(
    product_id: int, payload: list[BomLine], session: AsyncSession = Depends(get_db)
) -> list[ProductMaterial]:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )

    await session.execute(delete(ProductMaterial).where(ProductMaterial.product_id == product_id))
    lines = [ProductMaterial(product_id=product_id, material_id=l.material_id, qty_required=l.qty_required) for l in payload]
    session.add_all(lines)
    await session.commit()

    result = await session.execute(select(ProductMaterial).where(ProductMaterial.product_id == product_id))
    return list(result.scalars())


@router.get("/{product_id}/kitting-bom", response_model=list[KittingBomLineRead])
async def get_kitting_bom(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductKittingMaterial]:
    result = await session.execute(
        select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product_id)
    )
    return list(result.scalars())


@router.put("/{product_id}/kitting-bom", response_model=list[KittingBomLineRead])
async def replace_kitting_bom(
    product_id: int, payload: list[KittingBomLine], session: AsyncSession = Depends(get_db)
) -> list[ProductKittingMaterial]:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )

    await session.execute(delete(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product_id))
    lines = [
        ProductKittingMaterial(product_id=product_id, material_id=l.material_id, qty_required=l.qty_required)
        for l in payload
    ]
    session.add_all(lines)
    await session.commit()

    result = await session.execute(
        select(ProductKittingMaterial).where(ProductKittingMaterial.product_id == product_id)
    )
    return list(result.scalars())


@router.get("/{product_id}/bundle-items", response_model=list[BundleItemRead])
async def get_bundle_items(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductBundleItem]:
    result = await session.execute(
        select(ProductBundleItem).where(ProductBundleItem.bundle_product_id == product_id)
    )
    return list(result.scalars())


@router.put("/{product_id}/bundle-items", response_model=list[BundleItemRead])
async def replace_bundle_items(
    product_id: int, payload: list[BundleItem], session: AsyncSession = Depends(get_db)
) -> list[ProductBundleItem]:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    component_ids = [item.component_product_id for item in payload]
    if component_ids:
        result = await session.execute(select(Product).where(Product.id.in_(component_ids)))
        components = {p.id: p for p in result.scalars()}
        for component_id in component_ids:
            if component_id not in components:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Product {component_id} not found")
            if components[component_id].is_bundle:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A bundle's components cannot themselves be bundles",
                )
            if component_id == product_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A bundle cannot contain itself")

    await session.execute(delete(ProductBundleItem).where(ProductBundleItem.bundle_product_id == product_id))
    lines = [
        ProductBundleItem(bundle_product_id=product_id, component_product_id=item.component_product_id, qty=item.qty)
        for item in payload
    ]
    session.add_all(lines)
    await session.commit()

    result = await session.execute(select(ProductBundleItem).where(ProductBundleItem.bundle_product_id == product_id))
    return list(result.scalars())


@router.get("/{product_id}/variants", response_model=list[VariantRead])
async def list_variants(product_id: int, session: AsyncSession = Depends(get_db)) -> list[VariantRead]:
    product = await session.get(Product, product_id)
    result = await session.execute(
        select(ProductVariant).where(ProductVariant.product_id == product_id).order_by(ProductVariant.variant_name)
    )
    variants = list(result.scalars())
    if not variants:
        return []

    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    return await _variants_to_reads_bulk(session, product, variants, fee_source, fee_components, shipping_profiles_by_id)


async def _variants_to_reads_bulk(
    session: AsyncSession,
    product: Product | None,
    variants: list[ProductVariant],
    fee_source,
    fee_components,
    shipping_profiles_by_id: dict,
) -> list[VariantRead]:
    """Computes buildability/sellable numbers for every given variant (all belonging to
    the same product) in O(1) queries instead of one round-trip per variant — see
    compute_variants_buildability_bulk/compute_max_sellable_bulk docstrings. Buildability
    must finish before kitting starts: expected_max_sellable depends on each variant's
    own expected_max_buildable, so the two calls can't be parallelized."""
    product_id = product.id if product else variants[0].product_id
    variant_ids = [v.id for v in variants]
    buildability_by_variant = await compute_variants_buildability_bulk(session, product_id, variant_ids)
    expected_max_buildable_by_variant = {vid: buildability_by_variant[vid][1] for vid in variant_ids}
    sellable_by_variant = await compute_max_sellable_bulk(
        session,
        product_id,
        variants,
        expected_max_buildable_by_variant,
        product.platform_ceiling_qty if product else None,
    )

    reads = []
    for variant in variants:
        max_buildable, expected_max_buildable, cost_per_unit, effective_bom = buildability_by_variant[variant.id]
        max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason, effective_kitting_bom = (
            sellable_by_variant[variant.id]
        )
        full_sku = compute_full_sku(product.sku if product else None, variant.sku_suffix)
        effective_shipping_profile = resolve_variant_shipping_profile(shipping_profiles_by_id, variant, product)
        reads.append(
            VariantRead.model_validate(variant).model_copy(
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
                        fee_source, fee_components, variant, product, shipping_profiles_by_id
                    ),
                    "effective_shipping_profile_id": effective_shipping_profile.id if effective_shipping_profile else None,
                }
            )
        )
    return reads


@router.get("/{product_id}/builds", response_model=list[BuildRead])
async def list_builds(product_id: int, session: AsyncSession = Depends(get_db)) -> list[Build]:
    result = await session.execute(
        select(Build).where(Build.product_id == product_id).order_by(Build.built_at.desc(), Build.id.desc())
    )
    return list(result.scalars())


@router.get("/{product_id}/stock-adjustments", response_model=list[StockAdjustmentRead])
async def list_stock_adjustments(product_id: int, session: AsyncSession = Depends(get_db)) -> list[StockAdjustment]:
    result = await session.execute(
        select(StockAdjustment)
        .where(StockAdjustment.product_id == product_id)
        .order_by(StockAdjustment.created_at.desc(), StockAdjustment.id.desc())
    )
    return list(result.scalars())


@router.post("/{product_id}/variants/generate", response_model=list[VariantRead], status_code=status.HTTP_201_CREATED)
async def generate_product_variants(
    product_id: int, payload: GenerateVariantsRequest, session: AsyncSession = Depends(get_db)
) -> list[VariantRead]:
    created = await generate_variants(session, product_id, payload.attributes)
    if not created:
        return []
    product = await session.get(Product, product_id)
    fee_source, fee_components = await platform_fees.get_resolver_context(session)
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    return await _variants_to_reads_bulk(session, product, created, fee_source, fee_components, shipping_profiles_by_id)


async def _to_variant_read_with_buildability(session: AsyncSession, variant: ProductVariant) -> VariantRead:
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
    shipping_profiles_by_id = await get_shipping_profiles_by_id(session)
    effective_shipping_profile = resolve_variant_shipping_profile(shipping_profiles_by_id, variant, product)
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
                fee_source, fee_components, variant, product, shipping_profiles_by_id
            ),
            "effective_shipping_profile_id": effective_shipping_profile.id if effective_shipping_profile else None,
        }
    )


@router.post("/{product_id}/variants", response_model=VariantRead, status_code=status.HTTP_201_CREATED)
async def create_variant(
    product_id: int, payload: VariantCreate, session: AsyncSession = Depends(get_db)
) -> VariantRead:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    variant = ProductVariant(product_id=product_id, **payload.model_dump())
    session.add(variant)
    await session.commit()
    await session.refresh(variant)
    return await _to_variant_read_with_buildability(session, variant)
