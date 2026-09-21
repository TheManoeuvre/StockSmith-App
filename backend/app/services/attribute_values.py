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


# ---------------------------------------------------------------------------------------
# Merging two values
# ---------------------------------------------------------------------------------------


@dataclass
class ValuePair:
    """A loser-value variant and the survivor-value variant with the same other attributes."""

    loser: ProductVariant
    survivor: ProductVariant


@dataclass
class ValueMergeMatch:
    pairs: list[ValuePair]
    # Loser-value variants with no counterpart: they keep their row and simply take the
    # survivor value (a relabel, exactly as rename_value does).
    relabel_only: list[ProductVariant]


def _other_values(variant: ProductVariant, slot: int) -> tuple[str | None, ...]:
    return tuple(v for i, v in enumerate(_values_of(variant), start=1) if i != slot)


async def match_value_merge(
    session: AsyncSession, product_id: int, slot: int, loser_value: str, survivor_value: str
) -> ValueMergeMatch:
    """Which variants a value merge would touch, and how.

    Pairing is by the other two slots, exactly: "4 Stud Standard / Red" pairs with
    "4 Stud / Red". Disabled variants can be losers (they are relabelled or merged like
    any other, so the value really does disappear) but not survivors — a disabled
    counterpart is treated as absent and the loser is relabelled instead, which leaves
    both rows carrying the survivor value until one is reactivated and the pair merged.
    That collides with the combo unique constraint, so such a pair is refused up front.
    """
    await get_product_or_error(session, product_id)
    survivor_value = survivor_value.strip()
    if loser_value == survivor_value:
        raise AttributeValueError("Cannot merge a value into itself.")
    losers = await variants_with_value(session, product_id, slot, loser_value)
    if not losers:
        raise AttributeValueError(f'No variant of this product has "{loser_value}" in that attribute.')
    survivors = await variants_with_value(session, product_id, slot, survivor_value)
    if not survivors:
        raise AttributeValueError(f'No variant of this product has "{survivor_value}" in that attribute.')

    by_others = {_other_values(v, slot): v for v in survivors}
    pairs: list[ValuePair] = []
    relabel_only: list[ProductVariant] = []
    for loser in losers:
        counterpart = by_others.get(_other_values(loser, slot))
        if counterpart is None:
            relabel_only.append(loser)
        elif not counterpart.is_active:
            raise AttributeValueError(
                f'"{counterpart.variant_name}" is disabled — reactivate it (or disable "{loser.variant_name}" '
                "too) before merging these values."
            )
        else:
            pairs.append(ValuePair(loser=loser, survivor=counterpart))
    return ValueMergeMatch(pairs=pairs, relabel_only=relabel_only)


async def apply_value_merge(
    session: AsyncSession,
    product_id: int,
    slot: int,
    loser_value: str,
    survivor_value: str,
    *,
    bom: str = "keep_survivor",
    kitting: str = "keep_survivor",
    on_live_listing: str = "ask",
):
    """Folds every loser-value variant into its survivor-value counterpart (or relabels it
    when it has none), in one transaction.

    The live-listing question is answered for all pairs at once: the 409 lists every
    pair's listings, and "proceed" covers them all. The loser value's code row is left
    in place — it is retired, and a retired code is never reused (see
    ProductAttributeValueCode).
    """
    # Imported here: variant_merge imports nothing from this module, but keeping the
    # dependency one-way at module level costs nothing and avoids a cycle later.
    from app.services import variant_merge

    match = await match_value_merge(session, product_id, slot, loser_value, survivor_value)
    survivor_value = survivor_value.strip()

    # Every check before any write, so a refusal on the third pair leaves nothing half done.
    plans = [await variant_merge.plan_merge(session, p.loser.id, p.survivor.id) for p in match.pairs]
    blockers = sorted({b for plan in plans for b in plan.blockers})
    if blockers:
        raise AttributeValueError(" ".join(blockers))
    variant_merge.require_no_live_listing_conflicts(plans, on_live_listing)  # type: ignore[arg-type]

    outcomes = []
    for pair in match.pairs:
        outcomes.append(
            await variant_merge.apply_merge(
                session,
                pair.loser.id,
                pair.survivor.id,
                bom=bom,  # type: ignore[arg-type]
                kitting=kitting,  # type: ignore[arg-type]
                on_live_listing="proceed",  # already answered above, for all pairs
                commit=False,
            )
        )
    for variant in match.relabel_only:
        relabel_variant(variant, slot, survivor_value)

    await session.commit()
    return match, outcomes
