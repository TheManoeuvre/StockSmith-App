"""Whole-catalogue conformance scan against one platform's limits.

Answers the question a user actually has when they connect a new store: *if I start
listing on this platform, what in my catalogue breaks?* — before anything is pushed.

Deliberately scoped to a single platform rather than to the resolved cross-platform
limits. The enablement question is about one store, and reporting a violation Etsy
imposes inside eBay's panel would make it impossible to tell which store to act on. The
Integrations page renders one panel per connected platform, so the union is still visible.

Costs nothing outside this database: no adapter, no marketplace call, no rate-limit
budget. That is why it can be a plain GET the settings page loads on its own, rather than
sitting behind an explicit button like the listing gap scan (which does spend eBay's
Trading quota).

Three queries total regardless of catalogue size — products, variants, image counts —
then pure in-memory checks, following the shape of listing_sync.tracked_skus.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetType, ProductAsset
from app.models.listing import ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.platform_limits import (
    CatalogueCompatibilityReport,
    FieldViolation,
    ProductCompatibility,
    UnitCompatibility,
)
from app.services.platform_limits import (
    Severity,
    Violation,
    check_product,
    load_limit_table,
    resolve_effective_limits,
)


def _to_schema(violation: Violation) -> FieldViolation:
    return FieldViolation(
        field=violation.field,
        severity=violation.severity,
        current_value=violation.current_value,
        current_length=violation.current_length,
        limit=str(violation.limit.value),
        imposed_by=violation.limit.platform,
        message=violation.message,
        suggested_value=violation.suggested_value,
    )


async def scan_catalogue(session: AsyncSession, platform: ListingPlatform) -> CatalogueCompatibilityReport:
    """Checks every active product against one platform's limits.

    Only products with something to report are returned. A report listing every clean
    product alongside the two that need attention buries the answer — the counts carry
    the "everything else is fine" message.
    """
    table = await load_limit_table(session)
    effective = resolve_effective_limits({platform}, table)

    products = list(
        (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars()
    )
    variants = list(
        (
            await session.execute(
                select(ProductVariant)
                .join(Product, Product.id == ProductVariant.product_id)
                .where(Product.is_active.is_(True), ProductVariant.is_active.is_(True))
            )
        ).scalars()
    )
    image_counts = dict(
        (
            await session.execute(
                select(ProductAsset.product_id, func.count(ProductAsset.id))
                .where(ProductAsset.asset_type.in_([AssetType.main_image, AssetType.listing_image]))
                .group_by(ProductAsset.product_id)
            )
        ).all()
    )

    variants_by_product: dict[int, list[ProductVariant]] = {}
    for variant in variants:
        variants_by_product.setdefault(variant.product_id, []).append(variant)

    reported: list[ProductCompatibility] = []
    blocked = 0
    warned = 0

    for product in products:
        product_violations, units = check_product(
            product,
            variants_by_product.get(product.id, []),
            effective,
            {platform},
            image_count=image_counts.get(product.id, 0),
            table=table,
        )

        unit_reports = [
            UnitCompatibility(
                variant_id=unit.variant_id,
                variant_name=unit.variant_name,
                sku=unit.sku,
                violations=[_to_schema(v) for v in unit.violations],
            )
            for unit in units
            if unit.violations
        ]
        if not product_violations and not unit_reports:
            continue

        every = product_violations + [v for unit in units for v in unit.violations]
        has_blocker = any(v.severity == Severity.blocker for v in every)
        if has_blocker:
            blocked += 1
        else:
            warned += 1

        reported.append(
            ProductCompatibility(
                product_id=product.id,
                product_name=product.name,
                product_sku=product.sku,
                is_blocked=has_blocker,
                violations=[_to_schema(v) for v in product_violations],
                units=unit_reports,
            )
        )

    # Blocked first, then by name: the list is a worklist, and the things that stop a
    # listing existing at all deserve the top of it.
    reported.sort(key=lambda p: (not p.is_blocked, p.product_name.lower()))

    return CatalogueCompatibilityReport(
        platform=platform,
        total_products=len(products),
        blocked_count=blocked,
        warning_count=warned,
        products=reported,
    )
