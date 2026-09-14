"""Per-platform daily API-call accounting (Stage 2 of the listing-push rate-reduction
plan). The point of the counter is that listing_push and the reconcile sweep can stand
down before a burst of pushes exhausts a platform's budget and starves order sync — so
what matters here is that record()/flush() add up and the soft/hard thresholds fire.
"""

import pytest

from app.models.listing import ListingPlatform
from app.services import platform_api_usage


@pytest.fixture(autouse=True)
def _reset(monkeypatch, session_factory):
    # record() writes into a module-level dict and flush() persists via the module-level
    # session factory — point both at the test database and start from zero.
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    yield
    platform_api_usage._reset_for_tests()


async def test_record_then_flush_accumulates_into_one_row(session):
    platform_api_usage.record(ListingPlatform.etsy, 3)
    platform_api_usage.record(ListingPlatform.etsy)
    platform_api_usage.record(ListingPlatform.ebay, 5)

    assert await platform_api_usage.usage_today(session, ListingPlatform.etsy) == 4
    assert await platform_api_usage.usage_today(session, ListingPlatform.ebay) == 5

    # A later batch adds to the same day's row rather than replacing it.
    platform_api_usage.record(ListingPlatform.etsy, 10)
    assert await platform_api_usage.usage_today(session, ListingPlatform.etsy) == 14


async def test_soft_limit_trips_before_hard_limit(session):
    budget = platform_api_usage.daily_budget(ListingPlatform.etsy)

    platform_api_usage.record(ListingPlatform.etsy, int(budget * 0.81))
    assert await platform_api_usage.over_soft_limit(session, ListingPlatform.etsy) is True
    assert await platform_api_usage.over_hard_limit(session, ListingPlatform.etsy) is False

    platform_api_usage.record(ListingPlatform.etsy, int(budget * 0.15))
    assert await platform_api_usage.over_hard_limit(session, ListingPlatform.etsy) is True


async def test_usage_is_zero_and_under_budget_with_no_calls(session):
    assert await platform_api_usage.usage_today(session, ListingPlatform.ebay) == 0
    assert await platform_api_usage.over_soft_limit(session, ListingPlatform.ebay) is False


async def test_adapter_request_helpers_increment_the_counter(session, monkeypatch):
    """The counter is only useful if the adapters actually feed it — guard the wiring in
    Etsy._request_once and eBay._request_once."""
    import httpx

    from app.services.platforms.ebay import EbayAdapter
    from app.services.platforms.etsy import EtsyAdapter
    from app.models.platform_credential import PlatformEnvironment

    class _Conn:
        access_token = "t"

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, *a, **k):
            return httpx.Response(200, json={})

        def build_request(self, *a, **k):
            return httpx.Request("GET", "https://x")

        async def send(self, *a, **k):
            return httpx.Response(200, json={})

    monkeypatch.setattr("app.services.platforms.etsy.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr("app.services.platforms.ebay.httpx.AsyncClient", _FakeClient)

    await EtsyAdapter("i", "s")._request_once(_Conn(), "GET", "/x")
    await EbayAdapter("i", "s", PlatformEnvironment.production)._request_once(
        _Conn(), "GET", "https://api.ebay.com/x"
    )

    assert await platform_api_usage.usage_today(session, ListingPlatform.etsy) == 1
    assert await platform_api_usage.usage_today(session, ListingPlatform.ebay) == 1
