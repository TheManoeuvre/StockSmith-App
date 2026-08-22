from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.deps import get_db, require_auth
from app.models.build import Build
from app.models.kitting import ProductKittingMaterial
from app.models.order import Order, OrderLine
from app.models.product import Product, ProductBundleItem, ProductMaterial
from app.models.product_category import ProductCategory
from app.models.product_stock_event import ProductStockEvent
from app.models.stock_adjustment import StockAdjustment
from app.models.variant import ProductVariant
from app.schemas.build import BuildRead
from app.schemas.kitting import KittingBomLine, KittingBomLineRead
from app.schemas.stock_adjustment import StockAdjustmentRead
from app.schemas.stock_event import ProductStockEventRead
from app.models.pricing import ProductPriceSnapshot
from app.schemas.product import (
    BomLine,
    BomLineRead,
    BulkBomAmendChange,
    BulkBomAmendRequest,
    BulkBomAmendResult,
    BulkBomAmendUnit,
    BundleItem,
    BundleItemRead,
    GenerateVariantsRequest,
    ProductCreate,
    ProductPage,
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
    get_cost_per_unit_range_by_product,
    get_expected_max_buildable_by_product,
    get_max_buildable_by_product,
    get_ready_to_ship_by_bundle,
)
from app.services import abc, listing_push, platform_fees
from app.services.csv_io import export_products_csv, import_products_csv
from app.services.kitting import (
    _clamp_value_to_ceiling,
    apply_default_kitting_bom,
    apply_platform_ceiling,
    combine_expected_max_sellable,
    combine_max_sellable,
    combine_theoretical_max_sellable,
    compute_max_sellable,
    compute_max_sellable_bulk,
    get_expected_kitting_capacity_by_product,
    get_kitting_capacity_by_product,
    get_kitting_cost_per_unit_by_product,
    get_kitting_cost_per_unit_range_by_product,
    kitting_cost_per_unit_from_bom,
    sync_listing_ceiling_qty,
)
from app.services.pricing import snapshot_product_pricing
from app.services.shipping_profiles import (
    get_active_variant_profile_coverage_by_product,
    get_shipping_profiles_by_id,
    resolve_product_shipping_profile,
    resolve_shipping_cost_for_fee_source,
    resolve_variant_shipping_profile,
)
from app.services.validation import validate_lines_against_units
from app.services.variants import amend_attribute_bom_overrides, compute_full_sku, generate_variants

router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(require_auth)])

_MAIN_IMAGE_ASSET_ID_BY_PRODUCT_SQL = text(
    """
    SELECT id, product_id FROM (
        SELECT id, product_id,
               ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY display_order) AS rn
        FROM product_assets
        WHERE asset_type = 'main_image'
    ) ranked
    WHERE rn = 1
    """
)


async def _get_product_with_type(session: AsyncSession, product_id: int) -> Product:
    """Re-fetch with product_category loaded, mirroring materials' _get_material_with_manufacturer.

    ProductRead exposes product_category_name, and reading it off an unloaded relationship
    during response serialization raises MissingGreenlet rather than lazy-loading
    (services/allocation.py:19-24). A plain session.refresh(product, ["product_category"])
    isn't enough either: commit expires every attribute, and naming one leaves the rest
    expired to fail the same way on the next field.
    """
    result = await session.execute(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.product_category))
        .execution_options(populate_existing=True)
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


async def _get_main_image_asset_id_by_product(session: AsyncSession) -> dict[int, int]:
    result = await session.execute(_MAIN_IMAGE_ASSET_ID_BY_PRODUCT_SQL)
    return {row.product_id: row.id for row in result}


async def _cogs_incomplete_product_ids(
    session: AsyncSession,
    cost_per_unit_by_product: dict,
    cost_range_by_product: dict,
    bundle_cost_per_unit: dict,
) -> set[int]:
    """Every product missing something its orders need before they can report a truthful
    profit. Returned as a set so the same values drive both the per-row flag and the
    "Incomplete COGS only" filter — the two cannot drift apart.

    Two things count, and only two:

      * **No shipping profile on any sellable path.** The product sets none AND either it
        has no active variants or some active variant sets none either (see
        get_active_variant_profile_coverage_by_product for why the variant half matters).
        Such a product's orders reach ship_order with nothing to freeze, so postage silently
        costs £0 in _compute_net_profit. This is the gap that put roughly £95 of postage
        outside reported profit.
      * **No materials cost at all** — no base BOM and no variant with one. That is what
        leaves an order line with a NULL cost_per_unit_snapshot and produces the existing
        "COGS pending" badge.

    Deliberately NOT counted: a missing kitting BOM, because a product genuinely may have no
    packaging and the codebase is explicit that None means exactly that rather than "free";
    and a missing sale_price, which is a pricing gap rather than a COGS one. A flag that
    fires on legitimate configurations gets ignored, which would cost more than it's worth.
    """
    coverage = await get_active_variant_profile_coverage_by_product(session)
    rows = await session.execute(select(Product.id, Product.is_bundle, Product.shipping_profile_id))

    incomplete: set[int] = set()
    for product_id, is_bundle, shipping_profile_id in rows:
        active_count, uncovered_count = coverage.get(product_id, (0, 0))
        missing_postage = shipping_profile_id is None and (active_count == 0 or uncovered_count > 0)
        base_cost = bundle_cost_per_unit.get(product_id) if is_bundle else cost_per_unit_by_product.get(product_id)
        missing_materials = base_cost is None and cost_range_by_product.get(product_id) is None
        if missing_postage or missing_materials:
            incomplete.add(product_id)
    return incomplete


@dataclass(frozen=True)
class _ProductReadContext:
    """Every catalogue-wide lookup _read_product needs, gathered once.

    Exists because both list_products and get_product need the identical set, and passing
    them as positional arguments had already grown the signature past a dozen entries —
    adding the COGS lookups to that was how get_product silently fell out of step with
    list_products and started raising. Each lookup is a single aggregate over the whole
    products table (see build()), so gathering them for one product costs the same as for a
    page of fifty.
    """

    max_buildable_by_product: dict
    expected_max_buildable_by_product: dict
    cost_per_unit_by_product: dict
    kitting_cost_per_unit_by_product: dict
    cost_range_by_product: dict
    kitting_cost_range_by_product: dict
    cogs_incomplete_ids: set[int]
    main_image_asset_id_by_product: dict
    ready_to_ship_by_bundle: dict
    bundle_cost_per_unit: dict
    kitting_capacity_by_product: dict
    expected_kitting_capacity_by_product: dict
    active_variant_stock_totals_by_product: dict
    fee_source: object
    fee_components: object
    shipping_profiles_by_id: dict
    abc_rules: abc.Rules

    @staticmethod
    async def build(session: AsyncSession) -> "_ProductReadContext":
        cost_per_unit_by_product = await get_cost_per_unit_by_product(session)
        cost_range_by_product = await get_cost_per_unit_range_by_product(session)
        bundle_cost_per_unit = await get_bundle_cost_per_unit(session, cost_per_unit_by_product)
        fee_source, fee_components = await platform_fees.get_resolver_context(session)
        return _ProductReadContext(
            max_buildable_by_product=await get_max_buildable_by_product(session),
            expected_max_buildable_by_product=await get_expected_max_buildable_by_product(session),
            cost_per_unit_by_product=cost_per_unit_by_product,
            kitting_cost_per_unit_by_product=await get_kitting_cost_per_unit_by_product(session),
            cost_range_by_product=cost_range_by_product,
            kitting_cost_range_by_product=await get_kitting_cost_per_unit_range_by_product(session),
            cogs_incomplete_ids=await _cogs_incomplete_product_ids(
                session, cost_per_unit_by_product, cost_range_by_product, bundle_cost_per_unit
            ),
            main_image_asset_id_by_product=await _get_main_image_asset_id_by_product(session),
            ready_to_ship_by_bundle=await get_ready_to_ship_by_bundle(session),
            bundle_cost_per_unit=bundle_cost_per_unit,
            kitting_capacity_by_product=await get_kitting_capacity_by_product(session),
            expected_kitting_capacity_by_product=await get_expected_kitting_capacity_by_product(session),
            active_variant_stock_totals_by_product=await get_active_variant_stock_totals_by_product(session),
            fee_source=fee_source,
            fee_components=fee_components,
            shipping_profiles_by_id=await get_shipping_profiles_by_id(session),
            abc_rules=await abc.load_rules(session),
        )


def _read_product(product: Product, ctx: "_ProductReadContext") -> ProductRead:
    current_stock = product.current_stock
    allocated_qty = product.allocated_qty
    if product.is_bundle:
        max_buildable = None
        expected_max_buildable = None
        cost_per_unit = ctx.bundle_cost_per_unit.get(product.id)
        # A bundle has no kitting BOM of its own — packaging for one is whatever its
        # components' own BOMs say, which isn't a single per-unit figure.
        kitting_cost_per_unit = None
        ready_to_ship = ctx.ready_to_ship_by_bundle.get(product.id)
        max_sellable = None
        max_sellable_reason = None
        expected_max_sellable = None
        expected_max_sellable_reason = None
        theoretical_max_sellable = None
        theoretical_max_sellable_reason = None
    else:
        # A product with active variants never accumulates its own current_stock/
        # allocated_qty (builds always target the variant row) — use the summed variant
        # totals here instead, so this matches what the product detail page already
        # computes client-side, and so max_sellable's free_stock input is correct too.
        current_stock, allocated_qty = ctx.active_variant_stock_totals_by_product.get(
            product.id, (product.current_stock, product.allocated_qty)
        )
        max_buildable = ctx.max_buildable_by_product.get(product.id)
        expected_max_buildable = ctx.expected_max_buildable_by_product.get(product.id)
        cost_per_unit = ctx.cost_per_unit_by_product.get(product.id)
        kitting_cost_per_unit = ctx.kitting_cost_per_unit_by_product.get(product.id)
        ready_to_ship = None
        free_stock = current_stock - allocated_qty
        kitting_capacity = ctx.kitting_capacity_by_product.get(product.id)
        max_sellable, max_sellable_reason = combine_max_sellable(free_stock, kitting_capacity)
        expected_max_sellable, expected_max_sellable_reason = combine_expected_max_sellable(
            expected_max_buildable, ctx.expected_kitting_capacity_by_product.get(product.id)
        )
        theoretical_max_sellable, theoretical_max_sellable_reason = combine_theoretical_max_sellable(
            free_stock, max_buildable, kitting_capacity
        )
        max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason = apply_platform_ceiling(
            max_sellable, max_sellable_reason, expected_max_sellable, expected_max_sellable_reason,
            product.platform_ceiling_qty,
        )
        theoretical_max_sellable, theoretical_max_sellable_reason = _clamp_value_to_ceiling(
            theoretical_max_sellable, theoretical_max_sellable_reason, product.platform_ceiling_qty
        )
    shipping_profile = resolve_product_shipping_profile(ctx.shipping_profiles_by_id, product)
    effective_platform_fee_percent = platform_fees.resolve_fee_percent(
        ctx.fee_source,
        ctx.fee_components,
        product.platform_fee_percent,
        product.sale_price,
        shipping_profile.price if shipping_profile else None,
    )
    # A bundle's packaging is whatever its components' own BOMs say, so it has no kitting
    # figure of its own to take a range of either — same rule as kitting_cost_per_unit above.
    cost_range = ctx.cost_range_by_product.get(product.id)
    kitting_cost_range = None if product.is_bundle else ctx.kitting_cost_range_by_product.get(product.id)
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
            "theoretical_max_sellable": theoretical_max_sellable,
            "theoretical_max_sellable_reason": theoretical_max_sellable_reason,
            "cost_per_unit": cost_per_unit,
            "kitting_cost_per_unit": kitting_cost_per_unit,
            "cost_per_unit_min": cost_range[0] if cost_range else None,
            "cost_per_unit_max": cost_range[1] if cost_range else None,
            "kitting_cost_per_unit_min": kitting_cost_range[0] if kitting_cost_range else None,
            "kitting_cost_per_unit_max": kitting_cost_range[1] if kitting_cost_range else None,
            "main_image_asset_id": ctx.main_image_asset_id_by_product.get(product.id),
            "ready_to_ship": ready_to_ship,
            "effective_platform_fee_percent": effective_platform_fee_percent,
            "effective_shipping_profile_id": shipping_profile.id if shipping_profile else None,
            "effective_shipping_profile_name": shipping_profile.name if shipping_profile else None,
            "effective_shipping_cost": (
                resolve_shipping_cost_for_fee_source(shipping_profile, ctx.fee_source) if shipping_profile else None
            ),
            "cogs_incomplete": product.id in ctx.cogs_incomplete_ids,
            # Bundles are never counted (their quantity is derived from components), so
            # they carry no classification rather than a meaningless one.
            "classification": (
                None
                if product.is_bundle
                else abc.describe(ctx.abc_rules.for_product(product), product.last_stock_take_at)
            ),
        }
    )


@router.get("", response_model=ProductPage)
async def list_products(
    # le is generous rather than matching the Products list page's page size — several
    # other pages (bundle-item picker, manual order line picker, unmapped-SKU resolver)
    # need the *entire* catalog as a flat dropdown, not one page of it, and request it
    # via a single large limit rather than a separate unpaginated endpoint. Matches the
    # precedent in routers/materials.py's stock-history `limit` param.
    limit: int = Query(50, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    # Filtering server-side rather than in the client, because this list is paginated: a
    # client-side filter would only ever narrow the current page, and the total under it
    # would be wrong. Mirrors materials' ?material_type_id=.
    product_category_id: int | None = None,
    # Narrows to products with a COGS gap — see _cogs_incomplete_product_ids. Server-side
    # for the same reason as the category filter above: the list is paginated, so filtering
    # in the client would only ever narrow the current page and leave `total` wrong.
    cogs_incomplete: bool = False,
    session: AsyncSession = Depends(get_db),
) -> ProductPage:
    # Built before the count/page queries because the cogs_incomplete filter reads from it.
    # Every lookup inside is a single aggregate over the whole products table regardless of
    # how many rows this page returns — deliberately whole-catalogue rather than scoped to
    # this page's product IDs, which is also what lets the gap set be exact rather than
    # page-local. See _ProductReadContext.
    ctx = await _ProductReadContext.build(session)
    count_query = select(func.count()).select_from(Product)
    incomplete_count_query = select(func.count()).select_from(Product).where(Product.id.in_(ctx.cogs_incomplete_ids))
    # outerjoin so the ORDER BY below can read the category's name; products without one
    # keep their row rather than dropping out.
    query = (
        select(Product)
        .options(selectinload(Product.product_category))
        .outerjoin(ProductCategory, ProductCategory.id == Product.product_category_id)
    )
    if product_category_id is not None:
        count_query = count_query.where(Product.product_category_id == product_category_id)
        incomplete_count_query = incomplete_count_query.where(Product.product_category_id == product_category_id)
        query = query.where(Product.product_category_id == product_category_id)
    # Counted before the gap filter narrows anything, so the toggle can show how many
    # products it would reveal while it's still switched off.
    incomplete_total = await session.scalar(incomplete_count_query)
    if cogs_incomplete:
        count_query = count_query.where(Product.id.in_(ctx.cogs_incomplete_ids))
        query = query.where(Product.id.in_(ctx.cogs_incomplete_ids))
    total = await session.scalar(count_query)
    # selectinload rather than letting product_category_name touch the relationship lazily —
    # a lazy load in async raises MissingGreenlet (services/allocation.py:19-24).
    # Ordered by category, then name — so the list groups the way the materials list always
    # has, and so pagination cuts through that order cleanly rather than scattering one
    # category across every page. Uncategorised products sort last: NULLS LAST is not
    # portable, so this leans on a category id of NULL sorting after any real one.
    result = await session.execute(
        query.order_by(
            Product.product_category_id.is_(None),
            ProductCategory.name,
            Product.name,
        )
        .limit(limit)
        .offset(offset)
    )
    products = list(result.scalars())
    items = [_read_product(p, ctx) for p in products]
    return ProductPage(items=items, total=total or 0, incomplete_total=incomplete_total or 0)


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
    await apply_default_kitting_bom(session, product.id)
    await session.commit()
    return await _get_product_with_type(session, product.id)


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, session: AsyncSession = Depends(get_db)) -> ProductRead:
    product = await _get_product_with_type(session, product_id)
    read = _read_product(product, await _ProductReadContext.build(session))
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

    if {"platform_ceiling_qty", "push_buildable_capacity"} & changed_fields:
        # Both apply uniformly to the base product and every active variant's own
        # sellable figure (see kitting.apply_platform_ceiling and
        # listing_push._resolve_max_sellable) — push all of them, not just the bare
        # product row, so a toggle takes effect on the marketplace immediately rather
        # than waiting for the next unrelated stock change.
        listing_push.enqueue_for_owner(product)
        variant_ids = (
            await session.execute(
                select(ProductVariant.id).where(
                    ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)
                )
            )
        ).scalars()
        for variant_id in variant_ids:
            listing_push.enqueue_for_product(product_id, variant_id)

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
    return await _get_product_with_type(session, product_id)


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
    max_buildable_by_variant = {vid: buildability_by_variant[vid][0] for vid in variant_ids}
    expected_max_buildable_by_variant = {vid: buildability_by_variant[vid][1] for vid in variant_ids}
    sellable_by_variant = await compute_max_sellable_bulk(
        session,
        product_id,
        variants,
        expected_max_buildable_by_variant,
        product.platform_ceiling_qty if product else None,
        max_buildable_by_variant,
    )

    reads = []
    for variant in variants:
        max_buildable, expected_max_buildable, cost_per_unit, effective_bom = buildability_by_variant[variant.id]
        (
            max_sellable,
            max_sellable_reason,
            expected_max_sellable,
            expected_max_sellable_reason,
            theoretical_max_sellable,
            theoretical_max_sellable_reason,
            effective_kitting_bom,
        ) = sellable_by_variant[variant.id]
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
                    "theoretical_max_sellable": theoretical_max_sellable,
                    "theoretical_max_sellable_reason": theoretical_max_sellable_reason,
                    "cost_per_unit": cost_per_unit,
                    "kitting_cost_per_unit": kitting_cost_per_unit_from_bom(effective_kitting_bom),
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


@router.get("/{product_id}/stock-history", response_model=list[ProductStockEventRead])
async def list_stock_history(product_id: int, session: AsyncSession = Depends(get_db)) -> list[ProductStockEventRead]:
    """Unified "Stock" history: every build (success or failed), stock adjustment, and
    order fulfillment affecting this product/any of its variants, newest first, each
    carrying a running balance — replaces the old separate builds/stock-adjustments views."""
    result = await session.execute(
        select(ProductStockEvent, Build, StockAdjustment, OrderLine, Order)
        .outerjoin(Build, ProductStockEvent.source_build_id == Build.id)
        .outerjoin(StockAdjustment, ProductStockEvent.source_adjustment_id == StockAdjustment.id)
        .outerjoin(OrderLine, ProductStockEvent.source_order_line_id == OrderLine.id)
        .outerjoin(Order, OrderLine.order_id == Order.id)
        .where(ProductStockEvent.product_id == product_id)
        .order_by(ProductStockEvent.created_at.desc(), ProductStockEvent.id.desc())
    )
    reads = []
    for event, build, adjustment, order_line, order in result.all():
        reads.append(
            ProductStockEventRead.model_validate(event).model_copy(
                update={
                    "build_qty_built": build.qty_built if build else None,
                    "build_qty_failed": build.qty_failed if build else None,
                    "adjustment_mode": adjustment.mode if adjustment else None,
                    "adjustment_target_qty": adjustment.target_qty if adjustment else None,
                    "order_id": order_line.order_id if order_line else None,
                    "order_external_order_id": order.external_order_id if order else None,
                }
            )
        )
    return reads


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


@router.post("/{product_id}/variants/bom-overrides/amend", response_model=BulkBomAmendResult)
async def amend_variant_bom_overrides(
    product_id: int, payload: BulkBomAmendRequest, session: AsyncSession = Depends(get_db)
) -> BulkBomAmendResult:
    """Bulk-corrects BOM overrides across every variant sharing an attribute value.

    Product-scoped rather than variant-scoped because the target is a product + attribute
    value, not any one variant. Defaults to a preview (`apply: false`) — see
    services/variants.amend_attribute_bom_overrides for why that is the default rather
    than an option."""
    units, matched_count, skipped_inactive = await amend_attribute_bom_overrides(
        session,
        product_id,
        payload.attribute_name,
        payload.attribute_value,
        payload.lines,
        apply=payload.apply,
        include_inactive=payload.include_inactive,
    )

    if payload.apply:
        # Same reasoning as the single-variant path: a BOM change moves max_buildable,
        # which moves the quantity pushed to the marketplace. _enqueue dedupes and
        # debounces, so amending forty variants schedules forty tasks that drain safely.
        for variant, changes, _replaced, _new in units:
            if changes:
                listing_push.enqueue_for_product(product_id, variant.id)

    return BulkBomAmendResult(
        applied=payload.apply,
        matched_variant_count=matched_count,
        changed_variant_count=sum(1 for _v, changes, _r, _n in units if changes),
        skipped_inactive_count=skipped_inactive,
        units=[
            BulkBomAmendUnit(
                variant_id=variant.id,
                variant_name=variant.variant_name,
                changes=[BulkBomAmendChange(**c) for c in changes],
            )
            for variant, changes, _replaced, _new in units
        ],
    )


async def _to_variant_read_with_buildability(session: AsyncSession, variant: ProductVariant) -> VariantRead:
    product = await session.get(Product, variant.product_id)
    max_buildable, expected_max_buildable, cost_per_unit, effective_bom = await compute_variant_buildability(
        session, variant.product_id, variant.id
    )
    (
        max_sellable,
        max_sellable_reason,
        expected_max_sellable,
        expected_max_sellable_reason,
        theoretical_max_sellable,
        theoretical_max_sellable_reason,
        effective_kitting_bom,
    ) = await compute_max_sellable(
        session,
        variant.product_id,
        variant.id,
        variant.current_stock,
        variant.allocated_qty,
        expected_max_buildable,
        product.platform_ceiling_qty if product else None,
        max_buildable,
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
            "theoretical_max_sellable": theoretical_max_sellable,
            "theoretical_max_sellable_reason": theoretical_max_sellable_reason,
            "cost_per_unit": cost_per_unit,
            "kitting_cost_per_unit": kitting_cost_per_unit_from_bom(effective_kitting_bom),
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
