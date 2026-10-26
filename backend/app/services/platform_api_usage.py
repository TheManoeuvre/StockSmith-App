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

# Fallback daily request ceilings, used only until the provider reports its own (Etsy
# sends x-limit-per-day / x-remaining-today on every response — see observe_limits) and
# for platforms that never do. Etsy documents 10,000/day. eBay varies by
# call family; 5,000 is the conservative working figure this app already uses for the
# Trading API's tight budget (docs/plan-ebay-existing-store-onboarding.md). These are API
# facts, not deployment config, so they live in code rather than Settings.
#
# Square doesn't publish a comparable daily ceiling — its documented limits are per-second
# burst limits, not a daily cap — so 10,000 here is not a confirmed provider fact the way
# the other two are; it's the same conservative default this module falls back to for any
# unlisted platform, made explicit for a single in-person seller's realistic call volume
# (order sync is the only thing that calls Square today — no listing push exists for it).
# Revisit if Square-side throttling is ever actually observed.
_DAILY_BUDGET: dict[ListingPlatform, int] = {
    ListingPlatform.etsy: 10_000,
    ListingPlatform.ebay: 5_000,
    ListingPlatform.square: 10_000,
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

# Latest provider-reported quota per platform: (UTC date seen, daily limit, remaining).
_observed: dict[ListingPlatform, tuple[date, int, int]] = {}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def record(platform: ListingPlatform, count: int = 1) -> None:
    """Count `count` API calls against (platform, today). Cheap and sync — call it from
    inside an adapter's request helper without a session or an await."""
    if count:
        _pending_deltas[(platform, _today())] += count


def observe_limits(platform: ListingPlatform, daily_limit: int, remaining: int) -> None:
    """Record the provider's own view of today's quota (Etsy's x-limit-per-day /
    x-remaining-today response headers). Once seen, it replaces both the hard-coded
    budget and our local call tally for the rest of the UTC day — it is authoritative
    and also counts calls made by anything else sharing the API key.

    Responses can complete out of order under concurrency, so within a day (and while the
    limit is unchanged) the lowest `remaining` wins. Held in memory only: after a restart
    the fallbacks apply until the next response carries the headers again."""
    today = _today()
    previous = _observed.get(platform)
    if previous is not None and previous[0] == today and previous[1] == daily_limit:
        remaining = min(remaining, previous[2])
    _observed[platform] = (today, daily_limit, remaining)


def _observed_today(platform: ListingPlatform) -> tuple[int, int] | None:
    """(daily_limit, remaining) from today's provider headers, or None if not seen yet."""
    seen = _observed.get(platform)
    if seen is None or seen[0] != _today():
        return None
    return seen[1], seen[2]


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
    """Calls made to `platform` so far today. Uses the provider's own count (limit minus
    remaining) when it has reported one today, else our local tally including deltas not
    yet flushed."""
    observed = _observed_today(platform)
    if observed is not None:
        limit, remaining = observed
        return max(limit - remaining, 0)
    await flush()
    row = await session.get(PlatformApiUsage, (platform, _today()))
    return row.call_count if row is not None else 0


def daily_budget(platform: ListingPlatform) -> int:
    """Today's call ceiling: the provider-reported limit if seen, else the static fallback."""
    observed = _observed_today(platform)
    if observed is not None:
        return observed[0]
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
    _observed.clear()
