"""PATCH /variants/pricing — the one-request replacement for the variable-pricing form's
per-variant PATCH fan-out.

The bug this guards against was operational rather than logical: a group of a few hundred
variants saved as a few hundred concurrent PATCHes, which exhausted the SQLAlchemy pool and
failed part-way through with a generic error in the UI. So the assertions are about the
whole group landing in one call, nulls clearing rather than being skipped, and variants
outside the group being left alone.
"""

from decimal import Decimal

from app.models.product import Product
from app.models.variant import ProductVariant
from app.routers import variants as router
from app.schemas.variant import VariantPricingBulkUpdate


async def _product_with_variants(session, count: int) -> tuple[Product, list[ProductVariant]]:
    product = Product(name="Grouped", sku="SKU-GROUP")
    session.add(product)
    await session.flush()
    variants = [
        ProductVariant(
            product_id=product.id,
            variant_name=f"V{i}",
            attribute1_value=f"V{i}",
            sale_price=Decimal("1.00"),
            platform_fee_percent=Decimal("5.00"),
        )
        for i in range(count)
    ]
    session.add_all(variants)
    await session.commit()
    return product, variants


async def test_bulk_pricing_writes_every_listed_variant_and_no_other(session):
    _, variants = await _product_with_variants(session, 400)
    group, outsider = variants[:399], variants[399]

    await router.update_variant_pricing(
        VariantPricingBulkUpdate(
            variant_ids=[v.id for v in group], sale_price=Decimal("9.99"), platform_fee_percent=None
        ),
        session=session,
    )

    for v in group:
        await session.refresh(v)
        assert Decimal(v.sale_price) == Decimal("9.99")
        # None is a write, not an omission: clearing a group's manual fee is a real edit.
        assert v.platform_fee_percent is None
    await session.refresh(outsider)
    assert Decimal(outsider.sale_price) == Decimal("1.00")
    assert Decimal(outsider.platform_fee_percent) == Decimal("5.00")


async def test_bulk_pricing_with_no_ids_is_a_no_op(session):
    _, variants = await _product_with_variants(session, 2)
    await router.update_variant_pricing(
        VariantPricingBulkUpdate(variant_ids=[], sale_price=Decimal("9.99")), session=session
    )
    for v in variants:
        await session.refresh(v)
        assert Decimal(v.sale_price) == Decimal("1.00")
