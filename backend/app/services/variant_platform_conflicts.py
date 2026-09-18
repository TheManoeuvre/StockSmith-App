"""Warns, before a variant save, when the result would breach a target platform's limits.

The compatibility panel in Settings reports breaches after the fact; the draft-readiness
check refuses a push once one exists. Neither helps at the moment the user is about to
create the problem — generating a third attribute on a product Etsy will list, or the
combination that takes a product past Etsy's variation cap. This runs at that moment.

It is a question, not a refusal. A product can legitimately exceed one platform's limits
if the user intends to exclude that platform (ProductPlatformSettings.is_target) or has
an override coming, so the request is answered with a 409 that lists every conflict and
the client re-submits with on_platform_conflict="proceed" — the same shape as the
shared-material 409 in services/variants.generate_variants, and for the same reason: the
server has the numbers, the user has the intent.

Each target platform is checked on its own rather than through resolve_effective_limits.
The strictest-wins resolver answers "what must this value satisfy"; this has to answer
"which store objects, and by how much", so that the user can decide per store.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.variant import ProductVariant
from app.schemas.product import PlatformConflictResolution
from app.services.platform_limits import LimitField, default_limits, load_limit_table
from app.services.product_platforms import target_platforms

PLATFORM_LIMIT_CONFLICTS = "platform_limit_conflicts"

__all__ = [
    "PLATFORM_LIMIT_CONFLICTS",
    "PlatformConflictResolution",
    "active_variant_count",
    "find_platform_conflicts",
    "require_no_platform_conflicts",
]


def _platform_label(platform: ListingPlatform) -> str:
    return "eBay" if platform == ListingPlatform.ebay else platform.value.capitalize()


async def active_variant_count(session: AsyncSession, product_id: int) -> int:
    return (
        await session.execute(
            select(func.count(ProductVariant.id)).where(
                ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)
            )
        )
    ).scalar_one()


async def find_platform_conflicts(
    session: AsyncSession,
    product_id: int,
    *,
    attribute_count: int | None = None,
    active_variant_count: int | None = None,
) -> list[dict]:
    """Every (platform, field) pair the resulting counts would breach.

    Counts are what the product would have AFTER the save, not what is being added —
    the limit is on the listing, and the listing carries every active variant. A None
    count means that dimension isn't changing and is not checked.

    Returns plain dicts rather than a dataclass because they go straight into a 409
    detail; the message is a complete sentence in the house style (services/variants),
    naming the resulting count, the platform and its limit.
    """
    platforms = await target_platforms(session, product_id)
    if not platforms:
        return []
    table = await load_limit_table(session)

    checks: list[tuple[LimitField, int | None, str]] = [
        (LimitField.variation_attribute_max_count, attribute_count, "variation attributes"),
        (LimitField.variation_max_count, active_variant_count, "active variants on this product"),
    ]

    conflicts: list[dict] = []
    for platform in sorted(platforms, key=lambda p: p.value):
        limits = default_limits(platform, table)
        for field, count, noun in checks:
            limit = limits.get(field)
            if count is None or limit is None or count <= limit.int_value:
                continue
            conflicts.append(
                {
                    "platform": platform.value,
                    "field": field.value,
                    "resulting_count": count,
                    "limit": limit.int_value,
                    "message": (
                        f"This will result in {count} {noun}; "
                        f"{_platform_label(platform)} supports only {limit.int_value}."
                    ),
                }
            )
    return conflicts


async def require_no_platform_conflicts(
    session: AsyncSession,
    product_id: int,
    resolution: PlatformConflictResolution,
    *,
    attribute_count: int | None = None,
    active_variant_count: int | None = None,
) -> None:
    """Raises the structured 409 unless the caller has already said "proceed".

    Call this before anything is written: the whole point is that the user sees the
    conflict while cancelling still costs nothing.
    """
    if resolution == "proceed":
        return
    conflicts = await find_platform_conflicts(
        session, product_id, attribute_count=attribute_count, active_variant_count=active_variant_count
    )
    if not conflicts:
        return
    noun = "limit" if len(conflicts) == 1 else "limits"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": PLATFORM_LIMIT_CONFLICTS,
            "message": f"This would exceed {len(conflicts)} platform {noun}. Nothing has been saved.",
            "conflicts": conflicts,
        },
    )
