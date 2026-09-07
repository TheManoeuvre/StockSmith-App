"""Stage 4: the periodic reconcile sweep — the drift backstop that makes the Stage 3
"skip if unchanged" safe, and the retry the event-driven push path never had.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.services import listing_push, listing_reconcile, platform_api_usage


@pytest_asyncio.fixture
async def products(session):
    for pid in range(1, 6):
        session.add(Product(id=pid, name=f"P{pid}", sku=f"SKU-{pid}"))
    await session.flush()
    await session.commit()


def _listing(pid: int, **kw) -> Listing:
    return Listing(
        product_id=pid,
        variant_id=None,
        platform=kw.pop("platform", ListingPlatform.etsy),
        external_listing_id=kw.pop("external_listing_id", f"L{pid}"),
        **kw,
    )


@pytest.fixture(autouse=True)
def _wiring(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    monkeypatch.setattr(listing_reconcile, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    yield
    platform_api_usage._reset_for_tests()


async def test_picks_null_and_stale_skips_fresh_and_unlinked(session, products):
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            _listing(1, last_pushed_at=None),  # never pushed -> pick
            _listing(2, last_pushed_at=now - timedelta(hours=30)),  # stale -> pick
            _listing(3, last_pushed_at=now - timedelta(hours=1)),  # fresh -> skip
            _listing(4, last_pushed_at=None, external_listing_id=None),  # no live listing -> skip
        ]
    )
    await session.commit()

    cutoff = now - listing_reconcile._STALE_AFTER
    picked = await listing_reconcile._listings_to_check(session, ListingPlatform.etsy, cutoff)

    assert {l.product_id for l in picked} == {1, 2}
    # Never-pushed sorts ahead of merely-stale.
    assert picked[0].product_id == 1


async def test_includes_a_listing_whose_last_push_errored(session, products):
    now = datetime.now(timezone.utc)
    session.add(_listing(1, last_pushed_at=now - timedelta(minutes=5)))  # fresh by time
    session.add_all(
        [
            PlatformListingPush(
                product_id=1, variant_id=None, platform=ListingPlatform.etsy,
                attempted_qty=3, status=ListingPushStatus.success,
            ),
            PlatformListingPush(
                product_id=1, variant_id=None, platform=ListingPlatform.etsy,
                attempted_qty=3, status=ListingPushStatus.error, error_message="boom",
            ),
        ]
    )
    await session.commit()

    cutoff = now - listing_reconcile._STALE_AFTER
    picked = await listing_reconcile._listings_to_check(session, ListingPlatform.etsy, cutoff)

    assert [l.product_id for l in picked] == [1], "most recent attempt errored -> retry it"


async def test_run_is_capped_at_max_per_run(session, products, monkeypatch):
    monkeypatch.setattr(listing_reconcile, "_MAX_PER_RUN", 2)
    session.add_all([_listing(pid, last_pushed_at=None) for pid in range(1, 6)])
    await session.commit()

    cutoff = datetime.now(timezone.utc) - listing_reconcile._STALE_AFTER
    picked = await listing_reconcile._listings_to_check(session, ListingPlatform.etsy, cutoff)

    assert len(picked) == 2


async def test_reconcile_platform_noop_when_disconnected(session, products):
    session.add(_listing(1, last_pushed_at=None))
    await session.commit()
    calls: list = []
    import app.services.listing_push as lp

    async def _reconcile_listing(s, listing):
        calls.append(listing.product_id)

    # No PlatformConnection row exists -> disconnected.
    orig = lp.reconcile_listing
    lp.reconcile_listing = _reconcile_listing
    try:
        await listing_reconcile._reconcile_platform(ListingPlatform.etsy)
    finally:
        lp.reconcile_listing = orig

    assert calls == []


async def test_reconcile_platform_pushes_each_stale_listing(session, products, connection, monkeypatch):
    session.add_all([_listing(1, last_pushed_at=None), _listing(2, last_pushed_at=None)])
    await session.commit()

    seen: list[int] = []

    async def _reconcile_listing(s, listing):
        seen.append(listing.product_id)

    monkeypatch.setattr(listing_push, "reconcile_listing", _reconcile_listing)

    await listing_reconcile._reconcile_platform(ListingPlatform.etsy)

    assert sorted(seen) == [1, 2]


async def test_reconcile_platform_stands_down_over_hard_limit(session, products, connection, monkeypatch):
    session.add(_listing(1, last_pushed_at=None))
    await session.commit()
    platform_api_usage.record(
        ListingPlatform.etsy, platform_api_usage.hard_limit(ListingPlatform.etsy) + 1
    )

    called = False

    async def _reconcile_listing(s, listing):
        nonlocal called
        called = True

    monkeypatch.setattr(listing_push, "reconcile_listing", _reconcile_listing)

    await listing_reconcile._reconcile_platform(ListingPlatform.etsy)

    assert called is False


async def test_tick_flushes_usage_and_drains_deferred(session, monkeypatch):
    listing_push._deferred.add((9, None))
    enqueued: list = []
    monkeypatch.setattr(listing_push, "_enqueue", lambda p, v: enqueued.append((p, v)))
    platform_api_usage.record(ListingPlatform.etsy, 3)

    await listing_reconcile._tick()

    assert enqueued == [(9, None)]
    assert await platform_api_usage.usage_today(session, ListingPlatform.etsy) == 3
