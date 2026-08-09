"""Scheduled daily backups — one asyncio task, started and stopped from main.py's lifespan.

Modelled directly on sync_scheduler: a single loop, module-level state (one process serves the
whole desktop app), and configuration re-read every cycle so a settings change takes effect
without a reload signal. No APScheduler — a second scheduling model in a codebase that already
has a working one buys nothing.

The loop sleeps in short chunks rather than "until 03:00 tomorrow". Two reasons, both practical
on a personal machine: a settings change is picked up within one chunk instead of a day later,
and a laptop that was asleep at 03:00 backs up shortly after waking instead of skipping
entirely.
"""

import asyncio
import logging
from datetime import date, datetime

from app.db import async_session_factory
from app.services import backup

logger = logging.getLogger("stocksmith.backup_scheduler")

_TICK_SECONDS = 900  # 15 minutes

_task: asyncio.Task | None = None

# The date of the last successful scheduled run, so a due window that spans several ticks
# produces one backup rather than one per tick. Process-local, which is the right lifetime: a
# restart re-reads the last run from the database via _already_ran_today.
_last_scheduled_date: date | None = None


def _is_due(now: datetime, scheduled_hour: int, last_run: datetime | None) -> bool:
    """Due if we've reached today's hour and haven't already run since it came round.

    Compares against local calendar days deliberately — "back up at 3am" is a wall-clock
    intention, and the alternative (24h since the last run) drifts an hour later every day.
    """
    if now.hour < scheduled_hour:
        return False
    if last_run is None:
        return True
    # last_run is stored timezone-aware in UTC; compare in local time, since the schedule is
    # expressed in local hours.
    last_local = last_run.astimezone()
    return last_local.date() < now.date() or last_local.hour < scheduled_hour


async def _tick() -> None:
    global _last_scheduled_date

    if not backup.is_supported():
        return

    now = datetime.now()
    if _last_scheduled_date == now.date():
        return

    lock = backup.get_lock()
    if lock.locked():
        # A manual backup is in flight. Skip rather than queue — the next tick is 15 minutes
        # away and the user just took one anyway.
        return

    async with async_session_factory() as session:
        config = await backup.get_settings_row(session)
        if not config.scheduled_enabled:
            return
        if not _is_due(now, config.scheduled_hour_local, config.last_run_at):
            return

        async with lock:
            logger.info("Running scheduled backup")
            try:
                result = await backup.run_backup(session, kind="scheduled")
            except Exception:
                # run_backup has already recorded the failure on the settings row, which is what
                # surfaces in the UI. Logging here keeps it in backend.log too.
                logger.exception("Scheduled backup failed")
                return
        _last_scheduled_date = now.date()
        logger.info("Scheduled backup written: %s (%d bytes)", result.filename, result.size_bytes)


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in the backup scheduler loop")
        await asyncio.sleep(_TICK_SECONDS)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task, _last_scheduled_date
    if _task is not None:
        _task.cancel()
        _task = None
    # Cleared so a restart after a restore (which stops and starts the schedulers) re-evaluates
    # against the restored database's own last_run_at rather than this process's memory.
    _last_scheduled_date = None
