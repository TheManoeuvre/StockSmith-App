"""Reconnecting the same shop must not restart order sync from sync_start_date.

Disconnect used to clear last_orders_synced_at along with the tokens, so that a future
connection to a *different* shop could never inherit it. The far more common case is the
same shop reconnecting (a scope change, or plain troubleshooting), and for that shop the
wiped watermark meant the next sync re-fetched every receipt back to sync_start_date with
the Etsy adapter's per-receipt enrichment gate wide open — on 2026-09-14 that was ~120
already-imported orders at 10–20 API calls each, a sync that ran for most of an hour with
nothing to show for it, and two app restarts that each threw the progress away.

The watermark now survives a disconnect, and the different-shop case is handled where it
can actually be detected: the OAuth callback compares the fresh account id to the stored
one and resets only on a change.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.models.listing import ListingPlatform
from app.models.platform_credential import PlatformEnvironment
from app.routers import platforms as platforms_router
from app.services.platforms.base import TokenSet

_WATERMARK = datetime(2026, 9, 14, 12, 48, 19, tzinfo=timezone.utc)
_HOLD = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)


async def _refresh(session, connection):
    await session.refresh(connection)
    return connection


async def test_disconnect_keeps_the_watermark_and_account_id(session, connection):
    connection.last_orders_synced_at = _WATERMARK
    connection.unpaid_hold_since = _HOLD
    await session.commit()

    await platforms_router.disconnect_platform(ListingPlatform.etsy, session)

    connection = await _refresh(session, connection)
    # Genuinely disconnected — tokens gone, is_connected false, auto-sync off.
    assert connection.refresh_token is None
    assert connection.access_token is None
    assert connection.is_connected is False
    assert connection.auto_sync_enabled is False
    # ...but the sync position is retained for a same-shop reconnect.
    assert connection.last_orders_synced_at.replace(tzinfo=timezone.utc) == _WATERMARK
    assert connection.unpaid_hold_since.replace(tzinfo=timezone.utc) == _HOLD
    assert connection.external_account_id == "12345"


class _FakeOAuthAdapter:
    def __init__(self, account_id: str):
        self.account_id = account_id

    async def exchange_code(self, code, code_verifier, redirect_uri):
        return TokenSet(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes="listings_r transactions_r",
        )

    async def fetch_account_id(self, access_token):
        return self.account_id


@pytest.fixture
def complete_oauth(monkeypatch):
    """Drives platform_callback as the marketplace redirect would, with the network
    parts stubbed. Returns the callback's HTML response."""

    async def _run(session, account_id: str):
        adapter = _FakeOAuthAdapter(account_id)

        async def _get_adapter(*_args, **_kwargs):
            return adapter

        async def _redirect_uri(*_args, **_kwargs):
            return "http://localhost/callback"

        async def _no_shop_details(*_args, **_kwargs):
            return None

        monkeypatch.setattr(platforms_router, "get_adapter", _get_adapter)
        monkeypatch.setattr(platforms_router, "_redirect_uri", _redirect_uri)
        monkeypatch.setattr(platforms_router, "_enrich_etsy_shop_details", _no_shop_details)
        platforms_router._PENDING["state-1"] = ("verifier", time.time(), PlatformEnvironment.production)
        return await platforms_router.platform_callback(
            ListingPlatform.etsy, code="code", state="state-1", session=session
        )

    return _run


async def test_reconnecting_the_same_shop_keeps_the_watermark(session, connection, complete_oauth):
    connection.last_orders_synced_at = _WATERMARK
    await session.commit()
    await platforms_router.disconnect_platform(ListingPlatform.etsy, session)

    response = await complete_oauth(session, account_id="12345")

    assert response.status_code == 200
    connection = await _refresh(session, connection)
    assert connection.is_connected is True
    assert connection.external_account_id == "12345"
    assert connection.last_orders_synced_at.replace(tzinfo=timezone.utc) == _WATERMARK


async def test_reconnecting_as_a_different_shop_resets_the_watermark(session, connection, complete_oauth):
    """The case the old disconnect-time wipe was protecting: another shop's orders have
    nothing to do with where this one's sync had got to."""
    connection.last_orders_synced_at = _WATERMARK
    connection.unpaid_hold_since = _HOLD
    await session.commit()
    await platforms_router.disconnect_platform(ListingPlatform.etsy, session)

    await complete_oauth(session, account_id="99999")

    connection = await _refresh(session, connection)
    assert connection.external_account_id == "99999"
    assert connection.last_orders_synced_at is None
    assert connection.unpaid_hold_since is None


async def test_first_ever_connection_starts_with_no_watermark(session, complete_oauth):
    """No prior account id means nothing to compare against — and nothing to reset."""
    await complete_oauth(session, account_id="777")

    connection = await platforms_router._get_or_create_connection(
        session, ListingPlatform.etsy, PlatformEnvironment.production
    )
    assert connection.external_account_id == "777"
    assert connection.last_orders_synced_at is None
