"""The auto-sync loop must outlive anything that goes wrong inside one cycle.

A background loop that dies is the worst failure this app has, because it has no
symptom: the platform still reports itself connected with auto-sync on, no sync run is
logged (there was no attempt), and nothing appears in the log — _tasks holds a strong
reference to the task, which suppresses asyncio's own unretrieved-exception warning. The
first sign is missing orders, days later.
"""

import asyncio

import pytest

from app.models.listing import ListingPlatform
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
