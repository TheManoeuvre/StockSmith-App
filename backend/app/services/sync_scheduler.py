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
        return

    async with lock:
        async with async_session_factory() as session:
            try:
                await order_sync.commit_sync(session, platform)
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
    while True:
        try:
            await _tick(platform)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in %s auto-sync loop", platform.value)

        connection = await _load_connection(platform)
        interval_minutes = connection.sync_interval_minutes if connection is not None else 15
        await asyncio.sleep(max(interval_minutes, 1) * 60)


def start() -> None:
    for platform in (ListingPlatform.etsy, ListingPlatform.ebay):
        if platform not in _tasks:
            _tasks[platform] = asyncio.create_task(_loop(platform))


def stop() -> None:
    for task in _tasks.values():
        task.cancel()
    _tasks.clear()
