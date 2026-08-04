"""Cross-platform sync health, aggregated for the app's menu-bar indicator.

Deliberately separate from GET /platforms/{platform}/status: that endpoint is
per-platform, and answering it can trigger a best-effort Etsy shop-details HTTP fetch
(routers/platforms._enrich_etsy_shop_details), which makes it unsuitable as something the
UI polls on a timer. Everything here is local DB reads only — no marketplace I/O at any
price — precisely so the indicator can refresh often without spending API budget.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.platform_sync_run import PlatformSyncRun, SyncRunMode, SyncRunStatus
from app.schemas.platform import PlatformSyncSummary
from app.services.platforms.base import ensure_utc

# Mirrors the frontend's CONNECTABLE_PLATFORMS — platforms with a real adapter. Shopify is
# in the ListingPlatform enum for future use but has no adapter, so it can never have a
# connection or a sync run and would only ever render as a permanently-disconnected row.
_SUMMARISED_PLATFORMS = (ListingPlatform.etsy, ListingPlatform.ebay)


async def _latest_commit_runs(session: AsyncSession) -> dict[ListingPlatform, PlatformSyncRun]:
    """The most recent commit-mode run per platform, in one query.

    Preview runs are excluded for the same reason routers/platforms._latest_commit_run
    excludes them: they never write data, so a successful preview says nothing about
    whether auto-sync is actually working.
    """
    latest_started_at = (
        select(
            PlatformSyncRun.platform.label("platform"),
            func.max(PlatformSyncRun.started_at).label("started_at"),
        )
        .where(PlatformSyncRun.mode == SyncRunMode.commit)
        .group_by(PlatformSyncRun.platform)
        .subquery()
    )
    # Join back rather than using a window function: two runs of the same platform can
    # share a started_at (the column is only as precise as the DB's clock), and this way
    # the tie is broken by id — deterministically the later row.
    result = await session.execute(
        select(PlatformSyncRun)
        .join(
            latest_started_at,
            (PlatformSyncRun.platform == latest_started_at.c.platform)
            & (PlatformSyncRun.started_at == latest_started_at.c.started_at),
        )
        .where(PlatformSyncRun.mode == SyncRunMode.commit)
        .order_by(PlatformSyncRun.id)
    )
    return {run.platform: run for run in result.scalars()}


async def _failing_push_counts(session: AsyncSession) -> dict[ListingPlatform, int]:
    """How many listings are currently failing to receive quantity updates, per platform.

    "Currently" means the most recent attempt for that listing errored — not "errored at
    some point", and not a time window. That distinction matters because listing_push has
    no periodic reconciliation: a push that fails is never retried until something else
    changes that product's stock, so a failure from last week can still be the live state
    of the listing. A time-boxed count would quietly drop exactly the failures that have
    gone stale, which are the ones worth surfacing.
    """
    ranked = select(
        PlatformListingPush.platform.label("platform"),
        PlatformListingPush.status.label("status"),
        func.row_number()
        .over(
            partition_by=(
                PlatformListingPush.platform,
                PlatformListingPush.product_id,
                PlatformListingPush.variant_id,
            ),
            order_by=(PlatformListingPush.attempted_at.desc(), PlatformListingPush.id.desc()),
        )
        .label("rn"),
    ).subquery()

    result = await session.execute(
        select(ranked.c.platform, func.count())
        .where(ranked.c.rn == 1, ranked.c.status == ListingPushStatus.error)
        .group_by(ranked.c.platform)
    )
    return {platform: count for platform, count in result.all()}


async def get_sync_summary(session: AsyncSession) -> list[PlatformSyncSummary]:
    """One row per adapter-backed platform, connected or not — a disconnected platform is
    reported rather than omitted so the caller can distinguish "not set up" from "set up
    and silent", which look identical if the row simply isn't there."""
    connections = {
        c.platform: c
        for c in (await session.execute(select(PlatformConnection))).scalars()
    }
    latest_runs = await _latest_commit_runs(session)
    failing_pushes = await _failing_push_counts(session)

    summaries = []
    for platform in _SUMMARISED_PLATFORMS:
        connection = connections.get(platform)
        connected = connection is not None and connection.is_connected
        run = latest_runs.get(platform)
        summaries.append(
            PlatformSyncSummary(
                platform=platform,
                connected=connected,
                # Run history is reported even for a disconnected platform: "it last
                # synced an hour ago and then the connection dropped" is a materially
                # different story from "it has never synced", and hiding the timestamp
                # would collapse the two.
                #
                # ensure_utc because SQLite hands DateTime(timezone=True) back naive, and
                # the UI renders this as a relative time ("synced 4m ago") — an offsetless
                # timestamp is parsed as local by JS, which would skew the label by the
                # user's whole UTC offset.
                last_sync_at=ensure_utc(run.started_at) if run is not None else None,
                last_sync_status=run.status.value if run is not None else None,
                last_sync_error=(
                    run.error_message if run is not None and run.status == SyncRunStatus.error else None
                ),
                failing_push_count=failing_pushes.get(platform, 0),
            )
        )
    return summaries
