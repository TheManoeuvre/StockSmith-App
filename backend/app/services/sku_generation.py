"""Generating variant SKU suffixes that fit inside a marketplace's cap by construction.

The readable scheme — slugified attribute values joined with hyphens — produces a SKU whose
length depends on how long the values happen to be. "SKU-0037-6-STUD-SUNFLOWER-YELLOW" is
32 characters, exactly Etsy's cap, with nothing left for a third attribute or a longer
colour name. The next colour breaks it.

The numeric scheme replaces each value with a short allocated code, so length becomes a
function of how many attributes a product has rather than what they are called:
`SKU-0037-02-01-05` is 17 characters and stays that way.

Two rules that look like details and are not:

  * **Codes are allocated and stored, never derived from position.** See
    ProductAttributeValueCode — a positional code renumbers when a value is deleted, which
    silently rewrites the SKU of variants already live on a marketplace.

  * **The scheme is chosen per product, and never changes for a product that already has
    variants.** Adding one colour to a product whose SKUs are readable must not produce a
    numeric sibling; mixing the two within one product is worse than either alone. Only a
    product generating its first variants gets the numeric scheme.

Existing SKUs are never rewritten by any of this. Generation only ever creates combinations
that don't exist yet, so a product's established SKUs are structurally out of reach.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attribute_value_code import ProductAttributeValueCode
from app.models.listing import Listing, ListingPlatform
from app.models.variant import ProductVariant
from app.services.variants import slugify

# Two digits covers 99 values of one attribute on one product. The largest in the live
# catalogue uses 19. Codes past 99 simply render wider rather than wrapping — a SKU three
# characters longer is a far smaller problem than two variants sharing one.
_CODE_WIDTH = 2

# A suffix made only of digit groups, i.e. one this module produced.
_NUMERIC_SUFFIX = re.compile(r"^\d+(-\d+)*$")


def is_numeric_suffix(suffix: str | None) -> bool:
    return bool(suffix and _NUMERIC_SUFFIX.match(suffix))


def scheme_for(existing: list[ProductVariant]) -> str:
    """Which scheme this product's next variants should use.

    A product with no variants yet is new and gets numeric. A product with variants keeps
    whatever those already use, so a product is never half one scheme and half the other —
    which would be more confusing at the picking bench than either scheme on its own.
    """
    suffixed = [v.sku_suffix for v in existing if v.sku_suffix]
    if not suffixed:
        return "numeric"
    return "numeric" if all(is_numeric_suffix(s) for s in suffixed) else "readable"


async def allocate_codes(
    session: AsyncSession, product_id: int, wanted: list[tuple[int, str]]
) -> dict[tuple[int, str], int]:
    """Codes for every (slot, value) asked for, allocating any that don't exist yet.

    Allocation is max-so-far plus one, per slot — never "count of rows", which would reuse
    the code of a deleted value. A retired code stays retired because it may still be
    printed on a listing that exists.
    """
    rows = (
        await session.execute(
            select(ProductAttributeValueCode).where(ProductAttributeValueCode.product_id == product_id)
        )
    ).scalars().all()

    codes = {(r.attribute_slot, r.value): r.code for r in rows}
    next_by_slot: dict[int, int] = {}
    for row in rows:
        next_by_slot[row.attribute_slot] = max(next_by_slot.get(row.attribute_slot, 0), row.code)

    for slot, value in wanted:
        if (slot, value) in codes:
            continue
        nxt = next_by_slot.get(slot, 0) + 1
        next_by_slot[slot] = nxt
        codes[(slot, value)] = nxt
        session.add(
            ProductAttributeValueCode(
                product_id=product_id, attribute_slot=slot, value=value, code=nxt
            )
        )

    await session.flush()
    return codes


def numeric_suffix(codes: dict[tuple[int, str], int], combo: tuple[str, ...]) -> str:
    """The suffix for one combination, as zero-padded codes in attribute order."""
    return "-".join(str(codes[(slot, value)]).zfill(_CODE_WIDTH) for slot, value in enumerate(combo, start=1))


def readable_suffix(combo: tuple[str, ...]) -> str:
    """The original scheme, kept for products already using it."""
    return "-".join(slugify(v) for v in combo)


async def build_suffixes(
    session: AsyncSession,
    product_id: int,
    existing: list[ProductVariant],
    combos: list[tuple[str, ...]],
) -> dict[tuple[str, ...], str]:
    """Suffixes for each combination, in whichever scheme this product uses."""
    if scheme_for(existing) == "readable":
        return {combo: readable_suffix(combo) for combo in combos}

    wanted = [(slot, value) for combo in combos for slot, value in enumerate(combo, start=1)]
    codes = await allocate_codes(session, product_id, wanted)
    return {combo: numeric_suffix(codes, combo) for combo in combos}


async def live_skus_elsewhere(
    session: AsyncSession,
    product_id: int,
    variant_id: int | None,
    excluding: ListingPlatform | None = None,
) -> dict[ListingPlatform, str]:
    """The SKUs this unit is confirmed to be published under, per platform.

    Reads `Listing.published_sku`, which records what a marketplace actually acknowledged
    rather than what StockSmith would compute today — and that distinction is the whole
    point. Re-deriving the SKU to answer "what is live?" asks the wrong question, because
    the derived value is precisely the thing about to change.

    Anything in this result is off limits: changing a SKU that a marketplace is already
    using breaks the link between a listing and the product behind it, and no local edit
    can repair the listing at the other end.
    """
    query = select(Listing).where(
        Listing.product_id == product_id, Listing.published_sku.is_not(None)
    )
    if variant_id is not None:
        query = query.where(Listing.variant_id == variant_id)
    if excluding is not None:
        query = query.where(Listing.platform != excluding)

    result = await session.execute(query)
    return {listing.platform: listing.published_sku for listing in result.scalars()}
