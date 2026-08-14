"""Resolving the title and description a listing should actually carry.

Three levels, most specific first:

    platform override  ??  shared listing copy  ??  the inventory name/description

The fallback to `name`/`description` is what makes this safe to add to a catalogue that
has never had listing copy written for it: every product resolves to something, and
filling in the shared copy later is an improvement rather than a prerequisite.

The platform level earns its place on the numbers rather than on principle. Etsy allows
140 title characters and eBay 80, so a title written to use Etsy's budget cannot also fit
eBay at the top end — one shared title is guaranteed to be wrong for one of them for any
product whose title runs long.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.listing_profile import ProductPlatformSettings
from app.models.product import Product


@dataclass(frozen=True)
class ResolvedCopy:
    title: str
    description: str | None
    # Which level each value came from, so the editor can show "inherited from the
    # product name" rather than silently presenting a fallback as though it were authored.
    title_source: str
    description_source: str


def _first(*candidates: tuple[str | None, str]) -> tuple[str | None, str]:
    for value, source in candidates:
        if value is not None and value.strip():
            return value, source
    return None, "missing"


def resolve_copy(
    product: Product, settings: ProductPlatformSettings | None
) -> ResolvedCopy:
    """Pure resolution from already-loaded rows, so a bulk caller can load settings for
    the whole catalogue in one query and resolve per product without another."""
    title, title_source = _first(
        (settings.listing_title if settings else None, "platform"),
        (product.listing_title, "shared"),
        (product.name, "product_name"),
    )
    description, description_source = _first(
        (settings.listing_description if settings else None, "platform"),
        (product.listing_description, "shared"),
        (product.description, "product_description"),
    )
    return ResolvedCopy(
        # product.name is NOT NULL, so title always resolves to something.
        title=title or product.name,
        description=description,
        title_source=title_source,
        description_source=description_source,
    )


async def get_settings(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> ProductPlatformSettings | None:
    return (
        await session.execute(
            select(ProductPlatformSettings).where(
                ProductPlatformSettings.product_id == product_id,
                ProductPlatformSettings.platform == platform,
            )
        )
    ).scalar_one_or_none()


async def resolve_for_product(
    session: AsyncSession, product: Product, platform: ListingPlatform
) -> ResolvedCopy:
    return resolve_copy(product, await get_settings(session, product.id, platform))
