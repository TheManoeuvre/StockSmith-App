"""Tests for services/sync_status.get_sync_health — the "was StockSmith actually running?"
reading behind the tray work (docs/plan-background-sync.md §7).

The thing under test is an inference, not a record: there is no uptime table, only the
absence of sync runs. So the cases that matter are the ones where absence means something
other than downtime — nothing scheduled, a single missed tick, a window with no history at
all — because each of those, read naively, produces a confident and wrong answer.
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.services.sync_status import get_sync_health

_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _connection(platform, *, auto_sync=True, interval=15, connected=True):
    return PlatformConnection(
        platform=platform,
        refresh_token="refresh" if connected else None,
        auto_sync_enabled=auto_sync,
        sync_interval_minutes=interval,
    )


def _run(platform, *, minutes_ago, mode=SyncRunMode.commit, status=SyncRunStatus.success):
    return PlatformSyncRun(
        platform=platform,
        mode=mode,
        status=status,
        started_at=_NOW - timedelta(minutes=minutes_ago),
        fetched_count=0,
        new_count=0,
        needs_mapping_count=0,
        shipped_count=0,
        skipped_unpaid_count=0,
    )


def _ticks_every(minutes, *, spanning_minutes, platform=ListingPlatform.etsy):
    """A run every `minutes` back to `spanning_minutes` ago — a machine that stayed up."""
    return [_run(platform, minutes_ago=offset) for offset in range(0, spanning_minutes + 1, minutes)]


@pytest_asyncio.fixture
async def etsy_auto_syncing(session):
    session.add(_connection(ListingPlatform.etsy))
    await session.commit()


async def test_unmeasurable_when_nothing_is_scheduled(session):
    """Silence with no auto-sync on says nothing about uptime. Reporting zero gaps would
    read as a perfect week, which is the one answer that must not come out of no data."""
    session.add(_connection(ListingPlatform.etsy, auto_sync=False))
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.measurable is False
    assert "auto-sync" in (health.reason or "").lower()
    assert health.gaps == []
    assert health.total_gap_minutes == 0


async def test_unmeasurable_when_connected_platform_has_no_runs_yet(session, etsy_auto_syncing):
    """A freshly-enabled connection has no history. That is "nothing to compare against",
    not "down for seven days" — and the difference is the whole first-run experience."""
    health = await get_sync_health(session, now=_NOW)

    assert health.measurable is False
    assert health.gaps == []
    # The threshold is still reported: the panel can say what it *would* measure.
    assert health.expected_interval_minutes == 15
    assert health.gap_threshold_minutes == 30


async def test_steady_ticking_reports_no_gaps(session, etsy_auto_syncing):
    session.add_all(_ticks_every(15, spanning_minutes=60 * 24 * 7))
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.measurable is True
    assert health.gaps == []
    assert health.total_gap_minutes == 0
    assert health.longest_gap_minutes == 0


async def test_a_single_missed_tick_is_not_an_outage(session, etsy_auto_syncing):
    """The scheduler skips a tick whose lock is held, and a sync can overrun its own
    interval. Both produce a stretch of exactly two intervals, so this is the case that
    decides whether the panel cries wolf every single day."""
    session.add_all([
        _run(ListingPlatform.etsy, minutes_ago=offset)
        for offset in range(0, 24 * 60 + 1, 15)
        if offset != 300  # one tick missing: a 30-minute stretch, exactly 2 × interval
    ])
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    assert [gap.minutes for gap in health.gaps] == []


async def test_an_overnight_outage_is_reported(session, etsy_auto_syncing):
    """The case this exists for: the PC restarted at 2am and nobody signed in until 8."""
    session.add_all([
        # Ticking normally either side of a six-hour hole, and covering the whole window so
        # the only gap in the reading is the hole itself.
        *[_run(ListingPlatform.etsy, minutes_ago=offset) for offset in range(0, 181, 15)],
        *[_run(ListingPlatform.etsy, minutes_ago=offset) for offset in range(9 * 60, 24 * 60 + 1, 15)],
    ])
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    assert len(health.gaps) == 1
    gap = health.gaps[0]
    assert gap.minutes == 6 * 60
    assert gap.started_at == _NOW - timedelta(hours=9)
    assert gap.ended_at == _NOW - timedelta(hours=3)
    assert health.total_gap_minutes == 6 * 60
    assert health.longest_gap_minutes == 6 * 60


async def test_downtime_right_now_is_reported(session, etsy_auto_syncing):
    """No trailing rows at all is what "it is down at this moment" looks like. Without
    bookending the window at `now`, the most important outage of the lot is invisible."""
    session.add_all([_run(ListingPlatform.etsy, minutes_ago=offset) for offset in range(240, 360, 15)])
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.gaps[-1].ended_at == _NOW
    assert health.gaps[-1].minutes == 240


async def test_downtime_before_the_first_run_in_the_window_is_reported(session, etsy_auto_syncing):
    """Equally, a window that opens on silence — the app was already off when the week
    started — has to count, or a long outage shrinks to whatever part of it we happened to
    have rows either side of."""
    session.add_all(_ticks_every(15, spanning_minutes=60))
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    assert health.gaps[0].started_at == _NOW - timedelta(days=1)
    assert health.gaps[0].ended_at == _NOW - timedelta(minutes=60)


async def test_a_failed_sync_still_proves_the_app_was_running(session, etsy_auto_syncing):
    """The distinction that makes this a liveness signal rather than a success one: a
    marketplace being down is not StockSmith being down, and conflating them would blame
    the wrong thing for a week of Etsy 500s."""
    session.add_all([
        *[
            _run(ListingPlatform.etsy, minutes_ago=offset, status=SyncRunStatus.error)
            for offset in range(0, 181, 15)
        ],
    ])
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    assert all(gap.ended_at <= _NOW - timedelta(hours=3) for gap in health.gaps)


async def test_preview_runs_do_not_count_as_uptime(session, etsy_auto_syncing):
    """A preview is a human clicking a button. Counting it would credit uptime to someone
    being sat at the machine — the opposite of what this measures."""
    session.add_all([
        _run(ListingPlatform.etsy, minutes_ago=0),
        _run(ListingPlatform.etsy, minutes_ago=180, mode=SyncRunMode.preview),
        _run(ListingPlatform.etsy, minutes_ago=360),
    ])
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    # The preview sits in the middle of the six-hour silence and must not split it in two.
    assert [gap.minutes for gap in health.gaps if gap.minutes == 360] == [360]


async def test_either_platform_ticking_counts_as_up(session):
    """Two platforms are two views of one process. A gap has to be silent on both — eBay
    alone syncing still means the app was running."""
    session.add_all([_connection(ListingPlatform.etsy), _connection(ListingPlatform.ebay)])
    await session.commit()
    session.add_all([
        *_ticks_every(15, spanning_minutes=60, platform=ListingPlatform.etsy),
        *[_run(ListingPlatform.ebay, minutes_ago=offset) for offset in range(60, 24 * 60, 15)],
    ])
    await session.commit()

    health = await get_sync_health(session, window_days=1, now=_NOW)

    assert health.gaps == []


async def test_threshold_follows_the_shortest_configured_interval(session):
    """Whichever platform ticks most often is the finest-grained proof of life available,
    so it sets the resolution."""
    session.add_all([
        _connection(ListingPlatform.etsy, interval=60),
        _connection(ListingPlatform.ebay, interval=30),
    ])
    await session.commit()
    session.add(_run(ListingPlatform.etsy, minutes_ago=0))
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.expected_interval_minutes == 30
    assert health.gap_threshold_minutes == 60


async def test_a_very_short_interval_still_has_a_floor(session):
    """A 1-minute interval would otherwise make every ordinary pause an "outage". Nothing
    here is precise enough to be worth reporting at that resolution."""
    session.add(_connection(ListingPlatform.etsy, interval=1))
    await session.commit()
    session.add(_run(ListingPlatform.etsy, minutes_ago=0))
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.gap_threshold_minutes == 20


async def test_a_disconnected_platform_is_not_expected_to_tick(session):
    """Auto-sync left on behind a revoked connection isn't downtime — there is nothing to
    run. The scheduler itself stops after repeated auth failures, so expecting ticks here
    would report an outage for a connection nobody has reconnected yet."""
    session.add(_connection(ListingPlatform.etsy, connected=False))
    await session.commit()

    health = await get_sync_health(session, now=_NOW)

    assert health.measurable is False
