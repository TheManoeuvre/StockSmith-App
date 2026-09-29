"""Square is connected with a pasted personal access token, not an OAuth redirect (see
docs/plan-square-integration.md). These tests cover the three new endpoints
(connect_square, list_square_locations, set_square_location) rather than the OAuth
callback path the other platforms use.
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.platform_credential import PlatformEnvironment
from app.routers import platforms as platforms_router
from app.schemas.platform import SquareConnectRequest, SquareLocationRequest
from app.services.platforms.errors import PlatformAuthError, PlatformSyncError

_WATERMARK = datetime(2026, 9, 14, 12, 48, 19, tzinfo=timezone.utc)
_HOLD = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
_LOCATIONS = [
    {"id": "LTKZ9N895XRAY", "name": "Default Test Account"},
    {"id": "L2ND0CAT10N", "name": "Weekend Market Stall"},
]


def _fake_fetch_locations(monkeypatch, locations=None, error=None):
    async def _fetch(access_token, environment):
        if error is not None:
            raise error
        return locations if locations is not None else _LOCATIONS

    monkeypatch.setattr(platforms_router.square_client, "fetch_locations", _fetch)


async def test_connect_square_stores_token_and_returns_locations(session, monkeypatch):
    _fake_fetch_locations(monkeypatch, _LOCATIONS)

    result = await platforms_router.connect_square(
        SquareConnectRequest(access_token="sandbox-token", environment=PlatformEnvironment.sandbox),
        session=session,
    )

    assert [(o.id, o.label) for o in result] == [
        ("LTKZ9N895XRAY", "Default Test Account"),
        ("L2ND0CAT10N", "Weekend Market Stall"),
    ]
    connection = await platforms_router._get_or_create_connection(session, ListingPlatform.square, PlatformEnvironment.sandbox)
    assert connection.is_connected is True
    assert connection.access_token == "sandbox-token"
    assert connection.environment == PlatformEnvironment.sandbox
    # No location chosen yet — that's the separate /square/location step.
    assert connection.external_account_id is None


async def test_connect_square_rejects_a_bad_token(session, monkeypatch):
    _fake_fetch_locations(monkeypatch, error=PlatformAuthError("Square rejected this access token"))

    with pytest.raises(HTTPException) as exc_info:
        await platforms_router.connect_square(
            SquareConnectRequest(access_token="bad-token", environment=PlatformEnvironment.sandbox),
            session=session,
        )
    assert exc_info.value.status_code == 401

    # A rejected token must not leave any connection row behind.
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == ListingPlatform.square))
    assert result.scalar_one_or_none() is None


async def test_connect_square_surfaces_a_sync_error_as_502(session, monkeypatch):
    _fake_fetch_locations(monkeypatch, error=PlatformSyncError("Square returned an error: boom"))

    with pytest.raises(HTTPException) as exc_info:
        await platforms_router.connect_square(
            SquareConnectRequest(access_token="token", environment=PlatformEnvironment.sandbox),
            session=session,
        )
    assert exc_info.value.status_code == 502


@pytest.fixture
def square_connection_factory(session):
    async def _make(location_id: str | None = None) -> PlatformConnection:
        conn = PlatformConnection(
            platform=ListingPlatform.square,
            environment=PlatformEnvironment.sandbox,
            access_token="test-square-token",
            external_account_id=location_id,
        )
        session.add(conn)
        await session.commit()
        return conn

    return _make


async def test_list_square_locations_requires_a_connection(session):
    with pytest.raises(HTTPException) as exc_info:
        await platforms_router.list_square_locations(session=session)
    assert exc_info.value.status_code == 400


async def test_list_square_locations_uses_the_stored_token(session, monkeypatch, square_connection_factory):
    await square_connection_factory()
    _fake_fetch_locations(monkeypatch, _LOCATIONS)

    result = await platforms_router.list_square_locations(session=session)

    assert [o.id for o in result] == ["LTKZ9N895XRAY", "L2ND0CAT10N"]


async def test_set_square_location_stores_the_choice(session, square_connection_factory):
    connection = await square_connection_factory()

    await platforms_router.set_square_location(
        SquareLocationRequest(location_id="LTKZ9N895XRAY"), session=session
    )

    await session.refresh(connection)
    assert connection.external_account_id == "LTKZ9N895XRAY"


async def test_changing_square_location_resets_the_watermark(session, square_connection_factory):
    connection = await square_connection_factory(location_id="OLD-LOCATION")
    connection.last_orders_synced_at = _WATERMARK
    connection.unpaid_hold_since = _HOLD
    await session.commit()

    await platforms_router.set_square_location(
        SquareLocationRequest(location_id="NEW-LOCATION"), session=session
    )

    await session.refresh(connection)
    assert connection.external_account_id == "NEW-LOCATION"
    assert connection.last_orders_synced_at is None
    assert connection.unpaid_hold_since is None


async def test_reselecting_the_same_square_location_keeps_the_watermark(session, square_connection_factory):
    connection = await square_connection_factory(location_id="SAME-LOCATION")
    connection.last_orders_synced_at = _WATERMARK
    await session.commit()

    await platforms_router.set_square_location(
        SquareLocationRequest(location_id="SAME-LOCATION"), session=session
    )

    await session.refresh(connection)
    assert connection.last_orders_synced_at.replace(tzinfo=timezone.utc) == _WATERMARK


async def test_disconnect_square_keeps_the_location_and_watermark(session, square_connection_factory):
    connection = await square_connection_factory(location_id="LTKZ9N895XRAY")
    connection.last_orders_synced_at = _WATERMARK
    await session.commit()

    await platforms_router.disconnect_platform(ListingPlatform.square, session)

    await session.refresh(connection)
    assert connection.access_token is None
    assert connection.is_connected is False
    assert connection.external_account_id == "LTKZ9N895XRAY"
    assert connection.last_orders_synced_at.replace(tzinfo=timezone.utc) == _WATERMARK
