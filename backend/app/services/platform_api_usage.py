"""Per-platform, per-UTC-day marketplace API call accounting.

Every HTTP round-trip an adapter makes (services/platforms/*._request_once and the eBay
Trading equivalent) calls `record()` — a synchronous dict increment, no session, no
await, safe on the hot path. `flush()` folds the accumulated deltas into
`platform_api_usage` rows on a timer (services/listing_reconcile drives it) and before
any budget read.

Why this exists: on 2026-09-07 a burst of listing-push fan-out spent Etsy's whole daily
API budget by midday, and the next order-sync tick hit a 429 it couldn't recover from.
The budget helpers here let listing_push and the reconcile sweep stand down *before* that
point, while order sync — which must never be throttled — ignores them entirely.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timezone

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.platform_api_usage import PlatformApiUsage

logger = logging.getLogger("stocksmith.platform_api_usage")

# Provider-published daily request ceilings. Etsy documents 10,000/day. eBay varies by
# call family; 5,000 is the conservative working figure this app already uses for the
# Trading API's tight budget (docs/plan-ebay-existing-store-onboarding.md). These are API
# facts, not deployment config, so they live in code rather than Settings.
_DAILY_BUDGET: dict[ListingPlatform, int] = {
    ListingPlatform.etsy: 10_000,
    ListingPlatform.ebay: 5_000,
}
_DEFAULT_BUDGET = 10_000

# Automatic outbound pushes (listing_push's fan-out) stand down once the day's usage
# crosses this fraction of budget — leaving the rest for order sync, per-receipt
# enrichment and the reconcile sweep.
_SOFT_LIMIT_FRACTION = 0.80
# The reconcile sweep and deferred-push drain stand down at this fraction — a last
# reserve that only order sync may spend into.
_HARD_LIMIT_FRACTION = 0.95

# In-memory deltas not yet folded into the DB. Mutated only by record() (synchronous, no
# await, so increments are atomic under the single-threaded event loop) and drained by
# flush() under _flush_lock.
_pending_deltas: dict[tuple[ListingPlatform, date], int] = defaultdict(int)
_flush_lock = asyncio.Lock()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def record(platform: ListingPlatform, count: int = 1) -> None:
    """Count `count` API calls against (platform, today). Cheap and sync — call it from
    inside an adapter's request helper without a session or an await."""
    if count:
        _pending_deltas[(platform, _today())] += count


async def flush() -> None:
    """Fold the accumulated in-memory deltas into platform_api_usage rows."""
    async with _flush_lock:
        if not _pending_deltas:
            return
        deltas = dict(_pending_deltas)
        _pending_deltas.clear()
    try:
        async with async_session_factory() as session:
            for (platform, usage_date), delta in deltas.items():
                row = await session.get(PlatformApiUsage, (platform, usage_date))
                if row is None:
                    session.add(
                        PlatformApiUsage(platform=platform, usage_date=usage_date, call_count=delta)
                    )
                else:
                    row.call_count += delta
            await session.commit()
    except Exception:
        # Never lose counts to a transient DB error — fold them back in for the next flush.
        logger.exception("Failed to flush platform API usage counts; will retry next flush")
        for key, value in deltas.items():
            _pending_deltas[key] += value


async def usage_today(session, platform: ListingPlatform) -> int:
    """Calls made to `platform` so far today, including deltas not yet flushed."""
    await flush()
    row = await session.get(PlatformApiUsage, (platform, _today()))
    return row.call_count if row is not None else 0


def daily_budget(platform: ListingPlatform) -> int:
    return _DAILY_BUDGET.get(platform, _DEFAULT_BUDGET)


def soft_limit(platform: ListingPlatform) -> int:
    return int(daily_budget(platform) * _SOFT_LIMIT_FRACTION)


def hard_limit(platform: ListingPlatform) -> int:
    return int(daily_budget(platform) * _HARD_LIMIT_FRACTION)


async def over_soft_limit(session, platform: ListingPlatform) -> bool:
    """True once automatic outbound pushes for this platform should stand down."""
    return await usage_today(session, platform) >= soft_limit(platform)


async def over_hard_limit(session, platform: ListingPlatform) -> bool:
    """True once even the reconcile sweep / deferred-push drain should stand down."""
    return await usage_today(session, platform) >= hard_limit(platform)


def _reset_for_tests() -> None:
    _pending_deltas.clear()
