"""Renaming and merging the values of a product's variant attributes.

An attribute value has no row of its own. "4 Stud Standard" exists only as the string sitting
in `product_variants.attribute1_value` on each variant that has it, plus the SKU code row in
`product_attribute_value_codes` that pins its numeric code. So "rename a value" is a fan-out
UPDATE over those rows, and "merge two values" is a fan-out over variant *pairs* — the
variant with the loser value and the variant with the survivor value and the same other
attributes are the same thing spelled twice, and merging them is services/variant_merge's
job.

Two invariants this module never breaks:

  * **`sku_suffix` is never rewritten.** A variant's SKU may be printed on a listing that
    exists; renaming the value it was derived from does not change what the marketplace
    knows it as. See services/sku_generation.
  * **Codes are never reused.** A merged-away value keeps its code row so a future value
    cannot inherit its number — the same reason a deleted value does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attribute_value_code import ProductAttributeValueCode
from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant


class AttributeValueError(RuntimeError):
    """Refused. The message is user-facing."""


class ValueConflictError(AttributeValueError):
    """The new spelling is already a value in this slot, so the caller should offer a merge."""

    def __init__(self, message: str, existing_value: str):
        super().__init__(message)
        self.existing_value = existing_value


_VALUE_COLUMNS = ("attribute1_value", "attribute2_value", "attribute3_value")
_NAME_COLUMNS = ("variant_attribute1_name", "variant_attribute2_name", "variant_attribute3_name")


def value_column(slot: int) -> str:
    if slot not in (1, 2, 3):
        raise AttributeValueError("Attribute slot must be 1, 2 or 3.")
    return _VALUE_COLUMNS[slot - 1]


def derived_variant_name(values: tuple[str | None, ...]) -> str:
    """The name generate_variants gives a combination — the values joined with " / ".

    Used to tell a never-touched name (safe to regenerate after a value changes) from one
    the user edited by hand (left alone)."""
    return " / ".join(v for v in values if v)


def _values_of(variant: ProductVariant) -> tuple[str | None, str | None, str | None]:
    return (variant.attribute1_value, variant.attribute2_value, variant.attribute3_value)


def relabel_variant(variant: ProductVariant, slot: int, new_value: str) -> None:
    """Changes one slot's value on a variant in memory, regenerating the display name only
    when it was still the generated one. Does not touch sku_suffix."""
    before = _values_of(variant)
    setattr(variant, value_column(slot), new_value)
    if variant.variant_name == derived_variant_name(before):
        variant.variant_name = derived_variant_name(_values_of(variant))


async def get_product_or_error(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise AttributeValueError(f"No product with id {product_id}.")
    return product


async def variants_with_value(
    session: AsyncSession, product_id: int, slot: int, value: str, *, include_inactive: bool = True
) -> list[ProductVariant]:
    column = getattr(ProductVariant, value_column(slot))
    query = select(ProductVariant).where(ProductVariant.product_id == product_id, column == value)
    if not include_inactive:
        query = query.where(ProductVariant.is_active.is_(True))
    return list((await session.execute(query.order_by(ProductVariant.id))).scalars())


async def live_platforms(session: AsyncSession, product_id: int) -> list[ListingPlatform]:
    """Platforms with a confirmed listing for this product — where a rename will not show
    until the listing is next pushed."""
    result = await session.execute(
        select(Listing.platform)
        .where(Listing.product_id == product_id, Listing.external_listing_id.is_not(None))
        .distinct()
    )
    return sorted(result.scalars(), key=lambda p: p.value)


@dataclass
class RenameResult:
    variants_updated: int
    live_platforms: list[ListingPlatform] = field(default_factory=list)


async def rename_value(
    session: AsyncSession, product_id: int, slot: int, old_value: str, new_value: str
) -> RenameResult:
    """Respell one attribute value everywhere it appears on one product.

    Inactive variants are included deliberately: a disabled variant that is later
    reactivated must come back under the current spelling, not resurrect the old one and
    quietly become a fourth size.
    """
    await get_product_or_error(session, product_id)
    column_name = value_column(slot)
    new_value = new_value.strip()
    if not new_value:
        raise AttributeValueError("Value cannot be empty.")
    if new_value == old_value:
        return RenameResult(variants_updated=0, live_platforms=await live_platforms(session, product_id))

    clash = await variants_with_value(session, product_id, slot, new_value)
    if clash:
        raise ValueConflictError(
            f'"{new_value}" is already a value of this attribute.', existing_value=new_value
        )

    variants = await variants_with_value(session, product_id, slot, old_value)
    if not variants:
        raise AttributeValueError(f'No variant of this product has "{old_value}" in that attribute.')

    for variant in variants:
        relabel_variant(variant, slot, new_value)

    # Keep the code row in step so the next generate reuses this value's code rather than
    # allocating a fresh one for what is the same size under a new name. A code row for
    # new_value should not exist (the clash check established no variant carries it, and
    # codes are only allocated onto variants) — but if one somehow does, the old row is
    # simply left retired rather than colliding with it.
    existing_new = (
        await session.execute(
            select(ProductAttributeValueCode.id).where(
                ProductAttributeValueCode.product_id == product_id,
                ProductAttributeValueCode.attribute_slot == slot,
                ProductAttributeValueCode.value == new_value,
            )
        )
    ).scalar_one_or_none()
    if existing_new is None:
        await session.execute(
            update(ProductAttributeValueCode)
            .where(
                ProductAttributeValueCode.product_id == product_id,
                ProductAttributeValueCode.attribute_slot == slot,
                ProductAttributeValueCode.value == old_value,
            )
            .values(value=new_value)
        )

    platforms = await live_platforms(session, product_id)
    await session.commit()
    return RenameResult(variants_updated=len(variants), live_platforms=platforms)
