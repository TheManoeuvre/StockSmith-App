import asyncio
import logging

from sqlalchemy import select

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.services import order_sync
from app.services.platforms.errors import PlatformAuthError

logger = logging.getLogger("stocksmith.sync_scheduler")

"""Background auto-sync — one asyncio task per platform, each independently reading its
own connection's auto_sync_enabled/sync_interval_minutes fresh on every cycle (so an
interval change or a disconnect takes effect on the next tick without needing a live
reload signal). Deliberately not a shared cron-style poller: with only two platforms this
is simpler, and each loop's sleep duration can differ per platform without extra
bookkeeping.

Started/stopped from app/main.py's lifespan. A single process serves the whole desktop
app, so plain module-level state (the lock registry, the running tasks) is sufficient —
no need for anything cross-process."""

# One lock per platform, shared with the manual "Sync now" endpoint (routers/platforms.py)
# so the two can never run commit_sync concurrently for the same platform — avoids wasted
# API calls and interleaved PlatformSyncRun rows. The manual endpoint waits for the lock;
# the background loop below skips its tick instead of queuing up behind it (see _tick).
_locks: dict[ListingPlatform, asyncio.Lock] = {ListingPlatform.etsy: asyncio.Lock(), ListingPlatform.ebay: asyncio.Lock()}

# After this many consecutive PlatformAuthError results from the *background* loop
# specifically (manual syncs don't count), stop retrying every interval and flip
# auto_sync_enabled back off — a revoked/expired-past-refresh connection won't fix itself
# by being retried, and hammering it every cycle is just log noise.
_MAX_CONSECUTIVE_AUTH_FAILURES = 3

_tasks: dict[ListingPlatform, asyncio.Task] = {}


def get_lock(platform: ListingPlatform) -> asyncio.Lock:
    return _locks[platform]


async def _load_connection(platform: ListingPlatform) -> PlatformConnection | None:
    async with async_session_factory() as session:
        result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
        return result.scalar_one_or_none()


async def _record_auth_failure(platform: ListingPlatform) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
        connection = result.scalar_one_or_none()
        if connection is None:
            return
        connection.consecutive_auth_failures += 1
        if connection.consecutive_auth_failures >= _MAX_CONSECUTIVE_AUTH_FAILURES:
            connection.auto_sync_enabled = False
            logger.warning(
                "Disabling auto-sync for %s after %d consecutive auth failures — reconnect required",
                platform.value,
                connection.consecutive_auth_failures,
            )
        await session.commit()


async def _reset_auth_failures(platform: ListingPlatform) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
        connection = result.scalar_one_or_none()
        if connection is not None and connection.consecutive_auth_failures:
            connection.consecutive_auth_failures = 0
            await session.commit()


async def _tick(platform: ListingPlatform) -> None:
    connection = await _load_connection(platform)
    if connection is None or not connection.is_connected or not connection.auto_sync_enabled:
        return

    lock = get_lock(platform)
    if lock.locked():
        # A manual sync (or, in principle, a still-running previous tick) is already in
        # flight — skip this cycle rather than queuing up behind it; the next tick will
        # pick up wherever the watermark ends up.
        #
        # Logged rather than skipped silently: a sync that never returns holds this lock
        # forever, and every later tick then no-ops. That state is otherwise completely
        # invisible — no runs, no errors, auto-sync still showing as on — which is
        # indistinguishable from "the shop simply had no new orders" right up until
        # someone notices days of missing ones.
        logger.warning("Skipping %s auto-sync tick — a sync is already in flight", platform.value)
        return

    async with lock:
        try:
            # commit_sync manages its own short-lived sessions internally (see its
            # docstring) rather than taking one from here — deliberately, so this loop
            # running for both platforms right at every app boot never holds a pooled
            # connection open across the slow marketplace fetch phase.
            await order_sync.commit_sync(platform)
        except PlatformAuthError:
            # commit_sync has already rolled back and logged this to PlatformSyncRun
            # (see order_sync._record_failure) — this is purely for the
            # auto-disable counter.
            await _record_auth_failure(platform)
            return
        except Exception:
            logger.exception("Background auto-sync failed for %s", platform.value)
            return
    await _reset_auth_failures(platform)


async def _loop(platform: ListingPlatform) -> None:
    """Runs until cancelled at shutdown, and must never exit for any other reason.

    Everything the loop body does is inside the try, including reading the interval back
    out of the DB. That reload used to sit outside it, one statement past the handler —
    so a single transient failure there (a locked SQLite file mid-backup, a pool timeout)
    propagated out of the task and ended auto-sync for that platform until the app was
    next restarted. Nothing surfaced it either: _tasks holds a strong reference to the
    task forever, so asyncio's "Task exception was never retrieved" warning — the one
    thing that would have printed a traceback — never fires. The user-visible result is
    a platform that quietly stops syncing while still reporting itself as connected with
    auto-sync on — a failure with no symptom until someone notices missing orders.
    """
    while True:
        interval_minutes = 15
        try:
            await _tick(platform)

            connection = await _load_connection(platform)
            if connection is not None:
                interval_minutes = connection.sync_interval_minutes
        except asyncio.CancelledError:
            raise
        except Exception:
            # Falls through to the sleep below on the default interval rather than
            # retrying immediately — whatever failed is unlikely to be fixed microseconds
            # later, and a tight retry loop against a marketplace API is worse than a
            # missed cycle.
            logger.exception("Unexpected error in %s auto-sync loop — retrying next cycle", platform.value)

        await asyncio.sleep(max(interval_minutes, 1) * 60)


def _log_if_loop_ended(platform: ListingPlatform, task: asyncio.Task) -> None:
    """Last line of defence: _loop is written never to return, so if it ever does, that
    is a bug worth a log line rather than a platform that silently stops syncing.

    Needed because _tasks keeps a strong reference to every task, which suppresses
    asyncio's own "Task exception was never retrieved" warning — without this, a dead
    loop leaves no trace anywhere.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("%s auto-sync loop died — no further syncs until restart", platform.value, exc_info=error)
    else:
        logger.error("%s auto-sync loop returned unexpectedly — no further syncs until restart", platform.value)


def start() -> None:
    for platform in (ListingPlatform.etsy, ListingPlatform.ebay):
        if platform not in _tasks:
            task = asyncio.create_task(_loop(platform))
            task.add_done_callback(lambda t, p=platform: _log_if_loop_ended(p, t))
            _tasks[platform] = task


def stop() -> None:
    for task in _tasks.values():
        task.cancel()
    _tasks.clear()
