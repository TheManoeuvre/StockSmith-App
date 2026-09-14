"""The auto-sync loop must outlive anything that goes wrong inside one cycle.

A background loop that dies is the worst failure this app has, because it has no
symptom: the platform still reports itself connected with auto-sync on, no sync run is
logged (there was no attempt), and nothing appears in the log — _tasks holds a strong
reference to the task, which suppresses asyncio's own unretrieved-exception warning. The
first sign is missing orders, days later.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.notification import Notification, NotificationCategory
from app.services import sync_scheduler


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """The loop's own sleep is what makes it a loop — replace it with a cancellation so
    each test drives exactly one cycle and then stops deterministically."""

    async def _stop_after_one_cycle(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(sync_scheduler.asyncio, "sleep", _stop_after_one_cycle)


async def _run_one_cycle(platform=ListingPlatform.etsy):
    with pytest.raises(asyncio.CancelledError):
        await sync_scheduler._loop(platform)


async def test_a_failing_tick_does_not_end_the_loop(monkeypatch):
    calls = []

    async def _boom(platform):
        calls.append(platform)
        raise RuntimeError("marketplace fetch exploded")

    monkeypatch.setattr(sync_scheduler, "_tick", _boom)
    monkeypatch.setattr(sync_scheduler, "_load_connection", _never_called)

    # Reaching the sleep at all is the assertion: the tick's exception was handled and
    # the loop went on to wait for the next cycle.
    await _run_one_cycle()
    assert calls == [ListingPlatform.etsy]


async def test_a_failing_interval_reload_does_not_end_the_loop(monkeypatch):
    """The regression this file exists for.

    _load_connection ran outside the try, so a transient DB error here — a locked SQLite
    file during the nightly backup, a pool checkout timeout — killed the task outright
    instead of costing one cycle.
    """

    async def _ok(_platform):
        return None

    async def _db_is_locked(_platform):
        raise TimeoutError("QueuePool limit reached, connection timed out")

    monkeypatch.setattr(sync_scheduler, "_tick", _ok)
    monkeypatch.setattr(sync_scheduler, "_load_connection", _db_is_locked)

    await _run_one_cycle()


async def test_cancellation_still_stops_the_loop(monkeypatch):
    """The one exception that must get through — shutdown has to be able to stop this."""

    async def _cancelled(_platform):
        raise asyncio.CancelledError

    monkeypatch.setattr(sync_scheduler, "_tick", _cancelled)
    monkeypatch.setattr(sync_scheduler, "_load_connection", _never_called)

    await _run_one_cycle()


async def test_tick_skipped_while_a_sync_is_in_flight_is_logged(monkeypatch, caplog):
    """A held lock silently no-ops every subsequent tick, so it needs to say so — this is
    what a wedged sync looks like from the outside."""
    connection = _SimpleConnection()
    monkeypatch.setattr(sync_scheduler, "_load_connection", _returning(connection))

    lock = sync_scheduler.get_lock(ListingPlatform.etsy)
    async with lock:
        with caplog.at_level("WARNING", logger="stocksmith.sync_scheduler"):
            await sync_scheduler._tick(ListingPlatform.etsy)

    assert "already in flight" in caplog.text


async def test_disabled_auto_sync_tick_does_nothing(monkeypatch):
    """Guards the read of the flag itself: auto-sync off must skip before any adapter
    work, which is what makes a disconnect (which clears the flag) stop the loop dead
    without erroring."""
    monkeypatch.setattr(sync_scheduler, "_load_connection", _returning(_SimpleConnection(auto_sync_enabled=False)))

    async def _must_not_run(_platform):
        raise AssertionError("commit_sync must not be reached with auto-sync disabled")

    monkeypatch.setattr(sync_scheduler.order_sync, "commit_sync", _must_not_run)

    await sync_scheduler._tick(ListingPlatform.etsy)


async def test_a_wedged_commit_sync_is_abandoned_not_awaited_forever(monkeypatch):
    """The 2026-09-07 stall: commit_sync got stuck (a retry path sleeping on an
    hours-long Retry-After) and _loop, which awaits _tick to completion before it sleeps
    or iterates, froze for the whole platform with no failed run and no log line. _tick
    now bounds commit_sync with asyncio.wait_for; on timeout it records a failed run and
    returns so the loop lives on.
    """
    monkeypatch.setattr(sync_scheduler, "_load_connection", _returning(_SimpleConnection()))
    monkeypatch.setattr(sync_scheduler, "_COMMIT_SYNC_TIMEOUT_SECONDS", 0.05)

    async def _hang(_platform):
        # asyncio.sleep is monkeypatched to raise by the autouse fixture, so block on an
        # event that never fires instead — the point is a coroutine that never returns.
        await asyncio.Event().wait()

    recorded: list[tuple] = []

    async def _record(platform, mode, error):
        recorded.append((platform, mode, type(error).__name__))

    monkeypatch.setattr(sync_scheduler.order_sync, "commit_sync", _hang)
    monkeypatch.setattr(sync_scheduler.order_sync, "record_failed_run", _record)

    # Returns (rather than hangs or raises) — the loop would go on to its sleep.
    await sync_scheduler._tick(ListingPlatform.etsy)

    assert recorded == [(ListingPlatform.etsy, sync_scheduler.SyncRunMode.commit, "TimeoutError")]
    # Lock released, so the next tick (or a manual sync) isn't blocked behind the abandoned one.
    assert not sync_scheduler.get_lock(ListingPlatform.etsy).locked()


class TestReconnectRequiredAlert:
    """auto_sync_enabled flipping off after repeated auth failures is meant to be a
    one-time event from the outside, not something that re-alerts every cycle — see
    notification_alerts.raise_platform_reconnect_required_alert."""

    async def test_fires_once_when_auto_sync_disables_after_repeated_auth_failures(
        self, session, session_factory, monkeypatch, connection
    ):
        # _record_auth_failure/_tick build their own sessions from the module-level
        # factory rather than taking one from the caller — redirect it at the same
        # in-memory DB the `session`/`connection` fixtures use, same as conftest does for
        # order_sync/stock_takes.
        monkeypatch.setattr(sync_scheduler, "async_session_factory", session_factory)

        async def _boom(_platform):
            raise sync_scheduler.PlatformAuthError("token revoked")

        monkeypatch.setattr(sync_scheduler.order_sync, "commit_sync", _boom)

        async def _reconnect_notifications():
            result = await session.execute(
                select(Notification).where(Notification.category == NotificationCategory.platform_reconnect_required)
            )
            return list(result.scalars())

        # Three consecutive auth failures is _MAX_CONSECUTIVE_AUTH_FAILURES — the third
        # is what crosses the threshold and disables auto-sync.
        for _ in range(sync_scheduler._MAX_CONSECUTIVE_AUTH_FAILURES):
            await sync_scheduler._tick(ListingPlatform.etsy)

        notifications = await _reconnect_notifications()
        assert len(notifications) == 1
        assert "Etsy" in notifications[0].title

        await session.refresh(connection)
        assert connection.auto_sync_enabled is False

        # Further ticks must no-op entirely (and so raise no second alert): _tick bails
        # out on auto_sync_enabled before ever reaching commit_sync or _record_auth_failure.
        await sync_scheduler._tick(ListingPlatform.etsy)
        await sync_scheduler._tick(ListingPlatform.etsy)

        notifications = await _reconnect_notifications()
        assert len(notifications) == 1

    async def test_does_not_fire_before_the_threshold_is_crossed(self, session, session_factory, monkeypatch, connection):
        monkeypatch.setattr(sync_scheduler, "async_session_factory", session_factory)

        async def _boom(_platform):
            raise sync_scheduler.PlatformAuthError("token revoked")

        monkeypatch.setattr(sync_scheduler.order_sync, "commit_sync", _boom)

        for _ in range(sync_scheduler._MAX_CONSECUTIVE_AUTH_FAILURES - 1):
            await sync_scheduler._tick(ListingPlatform.etsy)

        result = await session.execute(
            select(Notification).where(Notification.category == NotificationCategory.platform_reconnect_required)
        )
        assert list(result.scalars()) == []

        await session.refresh(connection)
        assert connection.auto_sync_enabled is True


class _SimpleConnection:
    def __init__(self, auto_sync_enabled: bool = True):
        self.auto_sync_enabled = auto_sync_enabled
        self.sync_interval_minutes = 15
        self.is_connected = True


def _returning(value):
    async def _load(_platform):
        return value

    return _load


async def _never_called(_platform):
    raise AssertionError("should not be reached in this test")
