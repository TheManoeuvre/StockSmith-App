"""Stage 3 of the listing-push rate-reduction plan: only send a push when the number
would actually change, and never sleep the fan-out into the ground.

The 2026-09-07 blowout was ~1,100 pushes in a morning, ~2,400 API calls, almost none of
which changed anything on the marketplace — because the fan-out fired on a material
quantity moving, not on the pushed quantity moving, and the adapter did GET+PUT
unconditionally. These tests pin the two guards that fix that.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.services import listing_push, platform_api_usage


class _RecordingAdapter:
    def __init__(self):
        self.pushes: list[int] = []

    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        self.pushes.append(qty)


@pytest_asyncio.fixture
async def one_listing(session):
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.flush()
    listing = Listing(
        product_id=1,
        variant_id=None,
        platform=ListingPlatform.etsy,
        external_listing_id="L1",
    )
    session.add(listing)
    await session.commit()
    return listing


@pytest.fixture
def adapter(monkeypatch):
    a = _RecordingAdapter()

    async def _get_adapter(session, platform):
        return a

    monkeypatch.setattr(listing_push, "get_adapter", _get_adapter)
    return a


@pytest.fixture
def resolved(monkeypatch):
    """Pin _resolve_max_sellable so the test controls 'the number we'd send'."""
    box = {"qty": 7}

    async def _resolve(session, product_id, variant_id):
        return box["qty"]

    monkeypatch.setattr(listing_push, "_resolve_max_sellable", _resolve)
    return box


@pytest.fixture(autouse=True)
def _budget(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()
    yield
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()


async def test_first_push_sends_and_records_the_watermark(session, one_listing, adapter, resolved, connection):
    await listing_push._push_now(session, 1, None)

    assert adapter.pushes == [7]
    await session.refresh(one_listing)
    assert one_listing.last_pushed_qty == 7
    assert one_listing.last_pushed_at is not None


async def test_unchanged_quantity_is_skipped_entirely(session, one_listing, adapter, resolved, connection):
    one_listing.last_pushed_qty = 7
    one_listing.last_pushed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()

    await listing_push._push_now(session, 1, None)

    assert adapter.pushes == [], "no GET, no PUT when the marketplace already holds this number"


async def test_a_changed_quantity_is_still_sent(session, one_listing, adapter, resolved, connection):
    one_listing.last_pushed_qty = 5
    one_listing.last_pushed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()
    resolved["qty"] = 9

    await listing_push._push_now(session, 1, None)

    assert adapter.pushes == [9]


async def test_over_soft_limit_defers_instead_of_pushing(session, one_listing, adapter, resolved, connection):
    platform_api_usage.record(
        ListingPlatform.etsy, platform_api_usage.soft_limit(ListingPlatform.etsy) + 1
    )

    await listing_push._push_now(session, 1, None)

    assert adapter.pushes == []
    assert (1, None) in listing_push._deferred


async def test_drain_deferred_re_enqueues(monkeypatch):
    listing_push._deferred.add((4, 9))
    enqueued: list[tuple] = []
    monkeypatch.setattr(listing_push, "_enqueue", lambda p, v: enqueued.append((p, v)))

    count = await listing_push.drain_deferred()

    assert count == 1
    assert enqueued == [(4, 9)]
    assert not listing_push._deferred
