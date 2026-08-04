"""Tests for services/sync_status.get_sync_summary — the aggregate behind the menu-bar
sync indicator.

Worth testing at this level because every interesting case is an aggregation edge (latest
row per group, ties, "currently failing" vs "ever failed") that a hand-check of one seeded
row would not catch, and because the indicator's whole job is to be trusted at a glance:
an error badge that shows for a failure already fixed is worse than no badge at all.
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.sync_status import get_sync_summary

_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def push_targets(session):
    """Real products/variants for the push rows to point at — the test engine enforces
    foreign keys (see conftest), so the ids can't be invented."""
    products = [Product(id=1, name="Widget", sku="SKU-1"), Product(id=2, name="Gadget", sku="SKU-2")]
    session.add_all(products)
    await session.flush()
    session.add_all([
        ProductVariant(id=10, product_id=1, variant_name="Small"),
        ProductVariant(id=11, product_id=1, variant_name="Large"),
    ])
    await session.commit()


def _run(platform, status, *, minutes_ago=0, mode=SyncRunMode.commit, error=None):
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
        error_message=error,
    )


def _push(platform, status, *, product_id=None, variant_id=None, minutes_ago=0, error=None):
    return PlatformListingPush(
        product_id=product_id,
        variant_id=variant_id,
        platform=platform,
        attempted_qty=1,
        status=status,
        error_message=error,
        attempted_at=_NOW - timedelta(minutes=minutes_ago),
    )


def _by_platform(summaries):
    return {s.platform: s for s in summaries}


async def test_reports_every_adapter_backed_platform_even_with_no_data(session):
    """A platform that has never been connected must still appear — "not set up" and "set
    up but silent" are different problems and would look identical if the row vanished."""
    summaries = await get_sync_summary(session)

    assert {s.platform for s in summaries} == {ListingPlatform.etsy, ListingPlatform.ebay}
    for summary in summaries:
        assert summary.connected is False
        assert summary.last_sync_at is None
        assert summary.last_sync_status is None
        assert summary.failing_push_count == 0


async def test_picks_the_most_recent_run_per_platform(session):
    session.add_all([
        _run(ListingPlatform.etsy, SyncRunStatus.error, minutes_ago=90, error="old failure"),
        _run(ListingPlatform.etsy, SyncRunStatus.success, minutes_ago=5),
        _run(ListingPlatform.ebay, SyncRunStatus.success, minutes_ago=200),
        _run(ListingPlatform.ebay, SyncRunStatus.error, minutes_ago=10, error="ebay is down"),
    ])
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    # Etsy's newest run succeeded, so the older failure must not still be reported.
    assert summaries[ListingPlatform.etsy].last_sync_status == "success"
    assert summaries[ListingPlatform.etsy].last_sync_error is None
    assert summaries[ListingPlatform.etsy].last_sync_at == _NOW - timedelta(minutes=5)

    assert summaries[ListingPlatform.ebay].last_sync_status == "error"
    assert summaries[ListingPlatform.ebay].last_sync_error == "ebay is down"


async def test_preview_runs_are_ignored(session):
    """A preview writes nothing, so a successful one says nothing about whether auto-sync
    works — reporting it would mask a commit that is failing every time."""
    session.add_all([
        _run(ListingPlatform.etsy, SyncRunStatus.error, minutes_ago=30, error="commit failed"),
        _run(ListingPlatform.etsy, SyncRunStatus.success, minutes_ago=1, mode=SyncRunMode.preview),
    ])
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].last_sync_status == "error"
    assert summaries[ListingPlatform.etsy].last_sync_error == "commit failed"


async def test_ties_on_started_at_are_broken_by_id(session):
    """started_at is only as precise as the DB clock, so two runs can share one. Without a
    deterministic tie-break the badge would flicker between states on refresh."""
    session.add(_run(ListingPlatform.etsy, SyncRunStatus.error, error="first"))
    await session.commit()
    session.add(_run(ListingPlatform.etsy, SyncRunStatus.success))
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].last_sync_status == "success"


async def test_connected_reflects_refresh_token_presence(session):
    session.add(
        PlatformConnection(
            platform=ListingPlatform.etsy, access_token="a", refresh_token="r"
        )
    )
    session.add(PlatformConnection(platform=ListingPlatform.ebay, access_token="a", refresh_token=None))
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].connected is True
    assert summaries[ListingPlatform.ebay].connected is False


async def test_run_history_is_reported_even_when_disconnected(session):
    """"Synced an hour ago, then the connection dropped" is a different story from "never
    synced" — collapsing them would hide when the breakage started."""
    session.add(_run(ListingPlatform.etsy, SyncRunStatus.success, minutes_ago=60))
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].connected is False
    assert summaries[ListingPlatform.etsy].last_sync_at == _NOW - timedelta(minutes=60)


# --- Failing pushes ------------------------------------------------------------------


async def test_counts_only_listings_whose_latest_push_failed(session, push_targets):
    """The count is "what is broken now", not "what has ever broken" — a listing that
    failed and then succeeded is fixed, and must not keep the badge lit."""
    session.add_all([
        # Product 1: failed, then recovered — not currently failing.
        _push(ListingPlatform.etsy, ListingPushStatus.error, product_id=1, minutes_ago=60, error="boom"),
        _push(ListingPlatform.etsy, ListingPushStatus.success, product_id=1, minutes_ago=5),
        # Product 2: succeeded, then broke — currently failing.
        _push(ListingPlatform.etsy, ListingPushStatus.success, product_id=2, minutes_ago=60),
        _push(ListingPlatform.etsy, ListingPushStatus.error, product_id=2, minutes_ago=5, error="boom"),
    ])
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].failing_push_count == 1


async def test_failing_pushes_are_counted_per_variant_not_per_product(session, push_targets):
    """Two variants of one product are two separate listings — collapsing them would
    under-report how much of the catalogue is stale."""
    session.add_all([
        _push(ListingPlatform.ebay, ListingPushStatus.error, product_id=1, variant_id=10, error="a"),
        _push(ListingPlatform.ebay, ListingPushStatus.error, product_id=1, variant_id=11, error="b"),
    ])
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.ebay].failing_push_count == 2


async def test_old_failures_still_count(session, push_targets):
    """listing_push has no periodic retry: a push that failed last week is still the live
    state of that listing, so a time window would hide exactly the stalest failures."""
    session.add(
        _push(ListingPlatform.etsy, ListingPushStatus.error, product_id=1, minutes_ago=60 * 24 * 30, error="old")
    )
    await session.commit()

    summaries = _by_platform(await get_sync_summary(session))

    assert summaries[ListingPlatform.etsy].failing_push_count == 1


async def test_endpoint_returns_the_summary(session):
    """Covers the router wiring, not just the service — the endpoint is declared before
    the /{platform}/... routes specifically so "sync-summary" can't be parsed as a
    platform name, and nothing else would catch that regressing."""
    import app.routers.platforms as platforms_router

    session.add(_run(ListingPlatform.etsy, SyncRunStatus.error, minutes_ago=3, error="nope"))
    await session.commit()

    result = await platforms_router.get_sync_summary(session=session)

    by_platform = _by_platform(result)
    assert by_platform[ListingPlatform.etsy].last_sync_error == "nope"
    # Serialisable as declared — a naive datetime here would render as local time in the
    # UI's relative label, skewing it by the user's whole UTC offset.
    assert by_platform[ListingPlatform.etsy].last_sync_at.tzinfo is not None


async def test_push_failures_are_separate_from_order_sync_errors(session, push_targets):
    """A shop can import orders perfectly while silently failing to push stock back —
    that's the overselling case, and it must be visible even with a green sync."""
    session.add(_run(ListingPlatform.etsy, SyncRunStatus.success, minutes_ago=2))
    session.add(_push(ListingPlatform.etsy, ListingPushStatus.error, product_id=1, error="push failed"))
    await session.commit()

    summary = _by_platform(await get_sync_summary(session))[ListingPlatform.etsy]

    assert summary.last_sync_status == "success"
    assert summary.last_sync_error is None
    assert summary.failing_push_count == 1
