"""Adapter safety rails (Stage 1 of the listing-push rate-reduction plan).

Two failure modes from the 2026-09-07 Etsy stall, both at the transport layer:

  - a daily-quota 429 carries a `Retry-After` measured in *hours* (seconds until the
    quota window resets). Obeying it literally, up to _MAX_RATE_LIMIT_RETRIES times,
    parked the auto-sync loop for ~6h. The guard: never sleep longer than
    _RATE_LIMIT_MAX_SLEEP_SECONDS — raise PlatformRateLimitError instead so the caller
    records a failed run and retries next cycle.
  - a 401 that survives a token refresh was raised as a generic PlatformSyncError, which
    the scheduler's consecutive-auth-failure counter ignores, so a dead connection was
    retried every cycle forever. The guard: raise PlatformAuthError.
"""

import asyncio
import time

import httpx
import pytest

from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.etsy import EtsyAdapter
from app.models.platform_credential import PlatformEnvironment


class _Connection:
    access_token = "token"
    refresh_token = "refresh"
    access_token_expires_at = None
    last_orders_synced_at = None


async def _noop(*args, **kwargs):
    return None


def _etsy(monkeypatch) -> EtsyAdapter:
    adapter = EtsyAdapter("id", "secret")
    monkeypatch.setattr(adapter, "_ensure_fresh", _noop)
    monkeypatch.setattr(adapter, "_do_refresh", _noop)
    return adapter


def _ebay(monkeypatch) -> EbayAdapter:
    adapter = EbayAdapter("id", "secret", PlatformEnvironment.production)
    monkeypatch.setattr(adapter, "_ensure_fresh", _noop)
    monkeypatch.setattr(adapter, "_do_refresh", _noop)
    return adapter


def _responder(*status_and_headers):
    """Returns an async _request_once stub that yields the given responses in order,
    repeating the last one forever."""
    responses = [httpx.Response(code, headers=headers) for code, headers in status_and_headers]

    async def _request_once(*args, **kwargs):
        return responses[0] if len(responses) == 1 else responses.pop(0)

    return _request_once


async def test_etsy_refuses_a_multi_hour_retry_after(monkeypatch):
    adapter = _etsy(monkeypatch)
    monkeypatch.setattr(adapter, "_request_once", _responder((429, {"retry-after": "7200"})))

    slept: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s) or _noop())

    started = time.monotonic()
    with pytest.raises(PlatformRateLimitError, match="back-off"):
        await adapter._authed_request(None, _Connection(), "GET", "/shops/1/receipts")

    assert slept == [], "must not sleep on an hours-long Retry-After"
    assert time.monotonic() - started < 1


async def test_etsy_still_retries_a_short_retry_after(monkeypatch):
    adapter = _etsy(monkeypatch)
    monkeypatch.setattr(
        adapter, "_request_once", _responder((429, {"retry-after": "1"}), (200, {}))
    )
    slept: list[float] = []

    async def _fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    response = await adapter._authed_request(None, _Connection(), "GET", "/shops/1/receipts")
    assert response.status_code == 200
    assert slept == [1.0]


async def test_etsy_401_surviving_a_refresh_is_auth_error(monkeypatch):
    adapter = _etsy(monkeypatch)
    monkeypatch.setattr(adapter, "_request_once", _responder((401, {})))

    with pytest.raises(PlatformAuthError, match="reconnect required"):
        await adapter._authed_request(None, _Connection(), "GET", "/shops/1/receipts")


async def test_ebay_refuses_a_multi_hour_retry_after(monkeypatch):
    adapter = _ebay(monkeypatch)
    monkeypatch.setattr(adapter, "_request_once", _responder((429, {"retry-after": "3600"})))

    slept: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s) or _noop())

    with pytest.raises(PlatformRateLimitError, match="back-off"):
        await adapter._authed_request(None, _Connection(), "GET", "https://api.ebay.com/x")

    assert slept == []


async def test_ebay_401_surviving_a_refresh_is_auth_error(monkeypatch):
    adapter = _ebay(monkeypatch)
    monkeypatch.setattr(adapter, "_request_once", _responder((401, {})))

    with pytest.raises(PlatformAuthError, match="reconnect required"):
        await adapter._authed_request(None, _Connection(), "GET", "https://api.ebay.com/x")
