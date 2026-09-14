"""A sync that is still running must be visible as such.

The 2026-09-14 incident: a reconnected Etsy shop's first sync legitimately ran for the
better part of an hour (see test_reconnect_keeps_watermark), and for that whole stretch
the app said nothing — the run row was only written on completion, so the sync panel kept
showing the *previous* run as "succeeded", the scheduler's "already in flight" warning
pointed at nothing a user could see, and the user's response was to restart the app,
which threw the progress away and started it over. Twice.

These pin the `running` row: written before the fetch begins, finalised on every exit
path (success, error, timeout-cancellation, process death), and surfaced by the status
endpoint.
"""

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.routers import platforms as platforms_router
from app.schemas.platform import PlatformStatus, SyncRunRead
from app.services import order_sync
from app.services.platforms.errors import PlatformSyncError

from .conftest import make_order


async def _runs(session) -> list[PlatformSyncRun]:
    result = await session.execute(select(PlatformSyncRun).order_by(PlatformSyncRun.id))
    return list(result.scalars())


# --- Lifecycle ----------------------------------------------------------------------


async def test_running_row_exists_while_the_fetch_is_in_flight(session, session_factory, connection, monkeypatch):
    seen_mid_fetch: list[SyncRunStatus] = []

    class _ObservingAdapter:
        async def fetch_orders_since(self, _session, _connection, _since):
            async with session_factory() as s:
                seen_mid_fetch.extend(r.status for r in await _runs(s))
            return [make_order("R1")]

    async def _get_adapter(*_args, **_kwargs):
        return _ObservingAdapter()

    monkeypatch.setattr(order_sync, "get_adapter", _get_adapter)

    await order_sync.commit_sync(ListingPlatform.etsy)

    assert seen_mid_fetch == [SyncRunStatus.running]
    runs = await _runs(session)
    assert [r.status for r in runs] == [SyncRunStatus.success]
    assert runs[0].fetched_count == 1
    assert runs[0].finished_at is not None


async def test_a_failing_sync_finalises_the_same_row_as_error(session, session_factory, connection, monkeypatch):
    class _BrokenAdapter:
        async def fetch_orders_since(self, *_args):
            raise PlatformSyncError("Etsy is on fire")

    async def _get_adapter(*_args, **_kwargs):
        return _BrokenAdapter()

    monkeypatch.setattr(order_sync, "get_adapter", _get_adapter)

    with pytest.raises(PlatformSyncError):
        await order_sync.commit_sync(ListingPlatform.etsy)

    runs = await _runs(session)
    # One row, not a `running` one left dangling next to a fresh `error` one.
    assert [(r.status, r.error_message) for r in runs] == [(SyncRunStatus.error, "Etsy is on fire")]


async def test_timeout_path_finalises_the_running_row(session, session_factory, connection):
    """sync_scheduler's stall guard cancels commit_sync from outside, so commit_sync never
    reaches its own except-clause and can't pass the run id along. record_failed_run has
    to find the running row itself rather than adding a second row beside it."""
    run_id = await order_sync._open_run(ListingPlatform.etsy, SyncRunMode.commit)

    await order_sync.record_failed_run(
        ListingPlatform.etsy, SyncRunMode.commit, TimeoutError("Sync exceeded 600s and was abandoned")
    )

    runs = await _runs(session)
    assert len(runs) == 1
    assert runs[0].id == run_id
    assert runs[0].status == SyncRunStatus.error
    assert "abandoned" in (runs[0].error_message or "")


async def test_a_preview_failure_never_steals_a_running_commit_row(session, session_factory, connection):
    """Preview doesn't take the platform lock, so it can fail while a commit is mid-fetch.
    Its failure must land in its own row — mode is part of the running-row lookup."""
    commit_id = await order_sync._open_run(ListingPlatform.etsy, SyncRunMode.commit)

    await order_sync._record_failure(session, ListingPlatform.etsy, SyncRunMode.preview, httpx.ConnectTimeout(""))

    runs = {r.id: r for r in await _runs(session)}
    assert runs[commit_id].status == SyncRunStatus.running
    assert len(runs) == 2
    (preview_run,) = [r for r in runs.values() if r.mode == SyncRunMode.preview]
    assert preview_run.status == SyncRunStatus.error


async def test_orphaned_running_rows_are_closed_at_startup(session, session_factory, connection):
    """A process that dies mid-sync leaves its row `running` forever; the next boot has to
    close it, or the panel would report a sync in progress that no process is running."""
    orphan_id = await order_sync._open_run(ListingPlatform.etsy, SyncRunMode.commit)
    # A finished row must be left alone.
    await order_sync._record_failure(session, ListingPlatform.ebay, SyncRunMode.commit, PlatformSyncError("x"))

    closed = await order_sync.fail_orphaned_runs()

    assert closed == 1
    runs = {r.id: r for r in await _runs(session)}
    assert runs[orphan_id].status == SyncRunStatus.error
    assert "Interrupted" in (runs[orphan_id].error_message or "")
    assert runs[orphan_id].finished_at is not None
    assert await order_sync.fail_orphaned_runs() == 0


# --- Surfaced to the UI ------------------------------------------------------------


async def test_status_reports_a_running_sync_as_running(session, session_factory, connection):
    await order_sync._open_run(ListingPlatform.etsy, SyncRunMode.commit)

    status = await platforms_router._status_from_connection(session, ListingPlatform.etsy, connection)

    assert status.last_sync_status == SyncRunStatus.running
    assert status.last_sync_attempt_at is not None
    # Neither a success nor a failure yet — the panel used to infer "failed" from the
    # mismatch between these two.
    assert status.last_sync_success_at is None
    assert status.last_sync_error is None


def test_naive_timestamps_are_serialised_as_utc():
    """SQLite hands datetimes back naive; without an explicit offset the browser reads
    "15:01:27" as local time and a 16:01 BST sync displays as 15:01."""
    naive = datetime(2026, 9, 14, 15, 1, 27)
    run = SyncRunRead(
        id=1,
        platform=ListingPlatform.etsy,
        mode="commit",
        status="success",
        started_at=naive,
        finished_at=None,
        fetched_count=0,
        new_count=0,
        needs_mapping_count=0,
        shipped_count=0,
        skipped_unpaid_count=0,
        error_message=None,
    )
    assert run.started_at.tzinfo is timezone.utc
    assert run.model_dump(mode="json")["started_at"] == "2026-09-14T15:01:27Z"

    status = PlatformStatus(
        connected=True,
        account_id="1",
        shop_name=None,
        has_shop_icon=False,
        scopes=None,
        environment="production",
        connected_at=naive,
        sync_start_date=None,
        last_orders_synced_at=naive,
        last_refreshed_at=None,
        auto_sync_enabled=True,
        sync_interval_minutes=15,
        last_sync_attempt_at=naive,
        last_sync_success_at=naive,
        last_sync_error=None,
        unpaid_hold_since=None,
    )
    dumped = status.model_dump(mode="json")
    for field in ("connected_at", "last_orders_synced_at", "last_sync_attempt_at", "last_sync_success_at"):
        assert dumped[field] == "2026-09-14T15:01:27Z", field
    # Already-aware values pass through untouched.
    aware = PlatformStatus(**{**status.model_dump(), "connected_at": naive.replace(tzinfo=timezone.utc)})
    assert aware.connected_at == naive.replace(tzinfo=timezone.utc)
