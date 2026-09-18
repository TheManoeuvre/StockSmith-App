"""A push the marketplace can never accept as the listing is configured is recorded as
`blocked`, not `error`.

That one status is what stops the hourly reconcile sweep re-queueing it forever
(services/listing_reconcile._BLOCKED_RECHECK_AFTER) and what lets the menu-bar badge say
"go and change this listing" rather than "a retry is coming". These tests pin the path
from the adapter's exception to the row that gets written.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.services import listing_push, platform_api_usage
from app.services.platforms.errors import PlatformPushBlockedError, PlatformSyncError

_BLOCKED_MESSAGE = "This Etsy listing's quantity doesn't vary by variation"


class _RaisingAdapter:
    def __init__(self, error: Exception):
        self._error = error

    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        raise self._error


@pytest_asyncio.fixture
async def one_listing(session):
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.flush()
    listing = Listing(product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1")
    session.add(listing)
    await session.commit()
    return listing


@pytest.fixture
def raising(monkeypatch):
    def _install(error: Exception):
        async def _get_adapter(session, platform):
            return _RaisingAdapter(error)

        monkeypatch.setattr(listing_push, "get_adapter", _get_adapter)

    return _install


@pytest.fixture(autouse=True)
def _budget(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    yield
    platform_api_usage._reset_for_tests()


async def _statuses(session) -> list[tuple[ListingPushStatus, str | None]]:
    result = await session.execute(
        select(PlatformListingPush.status, PlatformListingPush.error_message).order_by(PlatformListingPush.id)
    )
    return list(result.all())


async def test_a_blocked_push_is_recorded_as_blocked_with_the_seller_facing_reason(
    session, one_listing, raising, connection
):
    raising(PlatformPushBlockedError(_BLOCKED_MESSAGE))

    status, message = await listing_push._push_one(session, one_listing, 7)

    assert status == ListingPushStatus.blocked
    assert message == _BLOCKED_MESSAGE
    assert await _statuses(session) == [(ListingPushStatus.blocked, _BLOCKED_MESSAGE)]


async def test_a_blocked_push_does_not_advance_the_watermark(session, one_listing, raising, connection):
    """Nothing was sent. Advancing last_pushed_qty would make _push_now's skip-if-unchanged
    gate hide the listing from every subsequent push — the failure would go quiet without
    ever having been fixed."""
    raising(PlatformPushBlockedError(_BLOCKED_MESSAGE))

    await listing_push._push_one(session, one_listing, 7)

    await session.refresh(one_listing)
    assert one_listing.last_pushed_qty is None
    assert one_listing.last_pushed_at is None


async def test_an_ordinary_failure_is_still_recorded_as_error(session, one_listing, raising, connection):
    raising(PlatformSyncError("502 Bad Gateway"))

    status, _ = await listing_push._push_one(session, one_listing, 7)

    assert status == ListingPushStatus.error
    assert (await _statuses(session))[0][0] == ListingPushStatus.error


async def test_push_units_now_reports_the_fix_rather_than_push_failed(
    session, one_listing, raising, connection, monkeypatch
):
    """The user clicked "Push corrections" and is waiting for an answer. "push failed"
    would send them looking for a fault in StockSmith; the listing's own setup is the
    blocker and the message says so."""
    raising(PlatformPushBlockedError(_BLOCKED_MESSAGE))

    async def _resolve(session, product_id, variant_id):
        return 7

    monkeypatch.setattr(listing_push, "_resolve_max_sellable", _resolve)

    pushed, errors = await listing_push.push_units_now(session, 1, [None])

    assert pushed == 0
    assert len(errors) == 1
    assert _BLOCKED_MESSAGE in errors[0]
