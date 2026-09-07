"""Periodic listing-push reconciliation — the drift backstop for the last_pushed_qty
watermark, and the retry the event-driven push path never had.

listing_push now skips a push outright when the marketplace already holds the resolved
quantity (services/listing_push._push_now). That is safe only if something else notices
when the two drift apart — a seller editing the listing by hand, a push that failed and
was never retried, or a listing whose stock simply never changes. This loop is that
something: a slow, bounded sweep that re-resolves each listing's target quantity and
pushes only on a real difference (the adapter GET-then-maybe-PUT does the comparison).

Modelled on sync_scheduler / backup_scheduler: one asyncio task, module-level state,
started and stopped from main.py's lifespan (and restart across a restore). It also
drives services/platform_api_usage.flush() and drains listing_push's budget-deferred
queue, so the API-budget accounting has a heartbeat.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import async_session_factory
from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.services import listing_push, platform_api_usage

logger = logging.getLogger("stocksmith.listing_reconcile")

_PLATFORMS = (ListingPlatform.etsy, ListingPlatform.ebay)

_TICK_SECONDS = 3600  # hourly — see _MAX_PER_RUN for why this cadence is affordable

# Wait this long after startup before the first sweep. sync_scheduler kicks off an order
# sync for both platforms the instant the app boots; letting that finish first keeps the
# sweep from racing it for API budget on a fresh install where every listing looks stale.
_STARTUP_DELAY_SECONDS = 300

# A listing whose last confirmed push is older than this is re-asserted on the next
# sweep. 12h means every listing is revisited roughly twice a day; with _MAX_PER_RUN
# below that keeps the sweep's own API-call cost to a few hundred a day, well inside the
# reserve platform_api_usage keeps back from the automatic-push soft limit.
_STALE_AFTER = timedelta(hours=12)

# Per platform, per tick. Bounds the sweep's budget spend and its wall-clock time. A
# truncated run is fine — it processed the most-stale listings first and the next tick
# continues.
_MAX_PER_RUN = 25

# A listing marked structurally unpushable (Listing.structural_push_block — e.g. an Etsy
# listing whose quantity doesn't vary by variation) is kept out of the normal selection
# above: retrying it every hour just burns a GET on a call that can't succeed. But the
# user may fix it on the marketplace at any time, so re-probe each marked listing on this
# slower cadence; a probe that finds it fixed pushes and clears the marker
# (services/listing_push._push_one), a probe that finds it still broken refreshes the
# timestamp and waits another window.
_STRUCTURAL_REPROBE_AFTER = timedelta(hours=24)
_MAX_STRUCTURAL_REPROBE_PER_RUN = 5

_task: asyncio.Task | None = None


async def _failing_targets(session, platform: ListingPlatform) -> set[tuple[int | None, int | None]]:
    """(product_id, variant_id) pairs whose most recent push attempt errored — the ones a
    purely event-driven push path would never retry on its own."""
    latest_id = (
        select(func.max(PlatformListingPush.id))
        .where(PlatformListingPush.platform == platform)
        .group_by(PlatformListingPush.product_id, PlatformListingPush.variant_id)
        .scalar_subquery()
    )
    result = await session.execute(
        select(PlatformListingPush.product_id, PlatformListingPush.variant_id).where(
            PlatformListingPush.id.in_(latest_id),
            PlatformListingPush.status == ListingPushStatus.error,
        )
    )
    return {(row.product_id, row.variant_id) for row in result}


async def _listings_to_check(session, platform: ListingPlatform, cutoff: datetime) -> list[Listing]:
    """Listings worth re-checking now: never pushed, pushed long enough ago to re-assert,
    or whose most recent push attempt errored. Most-stale first, capped at _MAX_PER_RUN.

    Structurally-unpushable listings (Listing.structural_push_block) are excluded here —
    _marked_to_reprobe handles those on a much slower cadence so an unfixable config
    doesn't consume a slot every hour."""
    failing = await _failing_targets(session, platform)

    result = await session.execute(
        select(Listing)
        .where(
            Listing.platform == platform,
            Listing.external_listing_id.is_not(None),
            Listing.structural_push_block.is_(None),
        )
        .order_by(Listing.last_pushed_at.is_(None).desc(), Listing.last_pushed_at.asc(), Listing.id.asc())
    )
    picked: list[Listing] = []
    for listing in result.scalars():
        is_stale = listing.last_pushed_at is None or _as_utc(listing.last_pushed_at) < cutoff
        is_failing = (listing.product_id, listing.variant_id) in failing
        if is_stale or is_failing:
            picked.append(listing)
        if len(picked) >= _MAX_PER_RUN:
            break
    return picked


async def _marked_to_reprobe(session, platform: ListingPlatform, now: datetime) -> list[Listing]:
    """Structurally-unpushable listings due a slow re-probe — marked longer ago than
    _STRUCTURAL_REPROBE_AFTER (or with no timestamp at all). Oldest mark first, capped
    small: this is a courtesy check for "did the user fix it yet", not a retry queue."""
    reprobe_cutoff = now - _STRUCTURAL_REPROBE_AFTER
    result = await session.execute(
        select(Listing)
        .where(
            Listing.platform == platform,
            Listing.external_listing_id.is_not(None),
            Listing.structural_push_block.is_not(None),
        )
        .order_by(Listing.structural_push_block_at.is_(None).desc(), Listing.structural_push_block_at.asc())
    )
    picked: list[Listing] = []
    for listing in result.scalars():
        if listing.structural_push_block_at is None or _as_utc(listing.structural_push_block_at) < reprobe_cutoff:
            picked.append(listing)
        if len(picked) >= _MAX_STRUCTURAL_REPROBE_PER_RUN:
            break
    return picked


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _listings_for_keys(
    session,
    platform: ListingPlatform,
    keys: list[tuple[int, int | None]],
    exclude_ids: set[int],
) -> list[Listing]:
    """Pushable listings on `platform` for the given (product_id, variant_id) keys — the
    upside-only moves Stage 6 parked on this sweep (listing_push._reconcile_soon) rather
    than dispatching immediately. Structurally-blocked ones are skipped, and anything
    already picked by the stale/failing selection is dropped via exclude_ids."""
    if not keys:
        return []
    key_set = set(keys)
    product_ids = {pid for pid, _ in key_set}
    result = await session.execute(
        select(Listing).where(
            Listing.platform == platform,
            Listing.product_id.in_(product_ids),
            Listing.external_listing_id.is_not(None),
            Listing.structural_push_block.is_(None),
        )
    )
    return [
        listing
        for listing in result.scalars()
        if listing.id not in exclude_ids and (listing.product_id, listing.variant_id) in key_set
    ]


async def _reconcile_platform(platform: ListingPlatform, reconcile_soon: list[tuple[int, int | None]] = ()) -> None:
    async with async_session_factory() as session:
        connection = (
            await session.execute(
                select(PlatformConnection).where(PlatformConnection.platform == platform)
            )
        ).scalar_one_or_none()
        if connection is None or not connection.is_connected:
            return
        if await platform_api_usage.over_hard_limit(session, platform):
            logger.warning(
                "Skipping %s reconcile sweep — daily API usage %d is past the reserve floor",
                platform.value,
                await platform_api_usage.usage_today(session, platform),
            )
            return

        now = datetime.now(timezone.utc)
        cutoff = now - _STALE_AFTER
        listings = await _listings_to_check(session, platform, cutoff)
        # Fold in the slow re-probe of structurally-blocked listings — same budget guard,
        # same per-listing loop. A push that goes through clears the marker; one that
        # still hits the block refreshes its timestamp so it waits another window.
        listings += await _marked_to_reprobe(session, platform, now)
        # Stage 6: upside-only quantity moves that were routed here instead of a
        # seconds-scale push. Appended after the stale/failing set, deduped against it.
        listings += await _listings_for_keys(
            session, platform, list(reconcile_soon), {l.id for l in listings}
        )
        if not listings:
            return

        checked = 0
        for listing in listings:
            if await platform_api_usage.over_hard_limit(session, platform):
                logger.warning("%s reconcile sweep stopped early — hit the API reserve floor", platform.value)
                break
            try:
                await listing_push.reconcile_listing(session, listing)
                checked += 1
            except Exception:
                logger.exception(
                    "Reconcile failed for %s listing product_id=%s variant_id=%s",
                    platform.value,
                    listing.product_id,
                    listing.variant_id,
                )
        logger.info("%s reconcile sweep checked %d listing(s)", platform.value, checked)


async def _tick() -> None:
    await platform_api_usage.flush()
    # Give budget-deferred pushes a chance to go out again. _push_now re-checks the soft
    # limit per listing, so anything still over budget simply bounces back into the queue
    # without an API call.
    drained = await listing_push.drain_deferred()
    if drained:
        logger.info("Re-enqueued %d listing push(es) that were deferred over API budget", drained)

    # Drain once here, not per platform, so a key with listings on both marketplaces is
    # offered to each _reconcile_platform call (each filters to its own).
    reconcile_soon = listing_push.take_reconcile_soon()

    for platform in _PLATFORMS:
        await _reconcile_platform(platform, reconcile_soon)


async def _loop() -> None:
    """Runs until cancelled at shutdown. Mirrors sync_scheduler._loop: every failure that
    is not cancellation is logged and costs one cycle, never the loop."""
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in the listing-reconcile loop — retrying next cycle")
        await asyncio.sleep(_TICK_SECONDS)


def _log_if_loop_ended(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Listing-reconcile loop died — no drift correction until restart", exc_info=error)
    else:
        logger.error("Listing-reconcile loop returned unexpectedly — no drift correction until restart")


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())
        _task.add_done_callback(_log_if_loop_ended)


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
