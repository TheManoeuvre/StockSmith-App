"""SquareAdapter's registration in services.platforms.get_adapter() and sync_scheduler —
the "wiring" step of docs/plan-square-integration.md, done after the adapter itself was
already built and tested in isolation (test_square_adapter.py).
"""

import asyncio

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.services import sync_scheduler
from app.services.platforms import get_adapter, invalidate_adapter_cache
from app.services.platforms.square import SquareAdapter


async def test_get_adapter_returns_a_square_adapter_with_no_app_credential_needed(session):
    """Unlike Etsy/eBay, Square needs no PlatformAppCredential row at all (no OAuth client
    id/secret — see routers/platforms.connect_square) — get_adapter must not demand one."""
    invalidate_adapter_cache(ListingPlatform.square)

    adapter = await get_adapter(session, ListingPlatform.square, PlatformEnvironment.sandbox)

    assert isinstance(adapter, SquareAdapter)


async def test_get_adapter_resolves_square_environment_from_its_connection(session):
    session.add(
        PlatformConnection(
            platform=ListingPlatform.square,
            environment=PlatformEnvironment.sandbox,
            access_token="tok",
            external_account_id="LOC1",
        )
    )
    await session.commit()
    invalidate_adapter_cache(ListingPlatform.square)

    # No explicit environment passed — must fall back to the connection's own, same as eBay.
    adapter = await get_adapter(session, ListingPlatform.square)

    assert isinstance(adapter, SquareAdapter)


def test_square_has_a_scheduler_lock():
    assert sync_scheduler.get_lock(ListingPlatform.square) is not None


async def test_square_is_included_in_the_scheduler_start_set(monkeypatch):
    """start() spawns one real never-returning loop per platform — stub _loop out so this
    only checks which platforms it's started for, without touching the real DB engine the
    way an unstubbed loop's first tick would."""
    started_for = []

    async def _fake_loop(platform):
        started_for.append(platform)
        await asyncio.Event().wait()  # never returns until cancelled, like the real loop

    monkeypatch.setattr(sync_scheduler, "_loop", _fake_loop)
    sync_scheduler.start()
    try:
        await asyncio.sleep(0)  # let the spawned tasks reach their first line
        assert set(started_for) == {ListingPlatform.etsy, ListingPlatform.ebay, ListingPlatform.square}
    finally:
        sync_scheduler.stop()


async def test_tick_skips_shipping_price_refresh_for_square(session, session_factory, monkeypatch):
    """Square has no shipping-profile integration — calling shipping_price_sync for it
    would raise NotConnectedError every cycle (see sync_scheduler._tick's guard)."""
    from app.schemas.platform import SyncCommitResult

    # _tick/_load_connection build their own sessions from the module-level factory rather
    # than taking one from the caller — redirect it at the same in-memory DB the `session`
    # fixture uses, same as test_sync_scheduler_resilience.py does.
    monkeypatch.setattr(sync_scheduler, "async_session_factory", session_factory)

    session.add(
        PlatformConnection(
            platform=ListingPlatform.square,
            environment=PlatformEnvironment.sandbox,
            access_token="tok",
            external_account_id="LOC1",
            auto_sync_enabled=True,
        )
    )
    await session.commit()

    async def _fake_commit_sync(platform):
        return SyncCommitResult(
            fetched_count=0, created_count=0, updated_count=0, needs_mapping_count=0,
            shipped_count=0, skipped_unpaid_count=0, order_ids=[],
        )

    refresh_calls = []

    async def _fake_refresh_if_due(platform):
        refresh_calls.append(platform)

    monkeypatch.setattr(sync_scheduler.order_sync, "commit_sync", _fake_commit_sync)
    monkeypatch.setattr(sync_scheduler.shipping_price_sync, "refresh_if_due", _fake_refresh_if_due)

    await sync_scheduler._tick(ListingPlatform.square)

    assert refresh_calls == []
