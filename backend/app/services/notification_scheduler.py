"""Background poller for everything notification-related that isn't a direct reactive hook:
material-forecast/order/backlog/API-usage alert checks, the digest flush, and the
daily/weekly order summary. Modelled directly on backup_scheduler — one asyncio task,
module-level state, configuration re-read every cycle. See that module's docstring for why
this shape (short-chunk sleep, no APScheduler) is the house style.

The two reactive alert hooks (marketplace sync failure, backup failure/secondary-location
failure) are NOT run from here — they're called directly from sync_scheduler.py and
backup.py at the moment those events happen, since polling for them would just be a slower,
noisier way to notice something the code already knows about instantly.
"""

import asyncio
import logging

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.services import notification_alerts
from app.services.notification_summary import maybe_fire_order_summary
from app.services.notifications import flush_digest, get_notification_settings

logger = logging.getLogger("stocksmith.notification_scheduler")

_TICK_SECONDS = 900  # 15 minutes — same cadence as backup_scheduler/sync_scheduler

_task: asyncio.Task | None = None


async def _tick() -> None:
    async with async_session_factory() as session:
        await notification_alerts.check_material_forecast_alerts(session)
        await notification_alerts.check_order_unfulfillable_alerts(session)
        await notification_alerts.check_pending_order_threshold(session)
        for platform in (ListingPlatform.etsy, ListingPlatform.ebay):
            await notification_alerts.check_platform_api_usage_alerts(session, platform)

        await flush_digest(session)

        settings = await get_notification_settings(session)
        await maybe_fire_order_summary(session, settings)


async def _loop() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in the notification scheduler loop")
        await asyncio.sleep(_TICK_SECONDS)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
