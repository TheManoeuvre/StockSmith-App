"""Listing rows for units a product no longer sells as — the product-level row on a
product that has since gained variants, or a variant row after the variant was
deactivated — must be invisible to every push path and to the "listings not receiving
stock updates" count.

Live case, 2026-09-19: product SKU-0033 was linked to its Etsy listing as a bare product
on 2026-08-03 and given two variants on 2026-08-05. The variant rows synced fine and the
Stores tab showed nothing wrong, but the orphaned product-level row was still picked by
the hourly reconcile sweep, pushed 'SKU-0033' to a listing that only carries
'SKU-0033-KMIX' and '-KMIX-XL', failed every hour, and was counted as one failing listing
in the sidebar — a warning nothing on the product page could explain.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import listing_push, listing_reconcile, listing_sync, platform_api_usage
from app.services.listing_units import current_unit_filter
from app.services.platforms.base import ExternalListingRef
from app.services.sync_status import _push_problem_counts


@pytest_asyncio.fixture
async def variant_product(session):
    """Product 1 sells as variants 10 (active) and 11 (deactivated); product 2 sells as
    itself. Every unit that ever existed has a linked Etsy row."""
    session.add_all([Product(id=1, name="Hanger", sku="SKU-1"), Product(id=2, name="Plain", sku="SKU-2")])
    await session.flush()
    session.add_all(
        [
            ProductVariant(id=10, product_id=1, variant_name="Std", sku_suffix="STD", is_active=True),
            ProductVariant(id=11, product_id=1, variant_name="Old", sku_suffix="OLD", is_active=False),
        ]
    )
    await session.flush()
    session.add_all(
        [
            Listing(product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1"),
            Listing(product_id=1, variant_id=10, platform=ListingPlatform.etsy, external_listing_id="L1"),
            Listing(product_id=1, variant_id=11, platform=ListingPlatform.etsy, external_listing_id="L1"),
            Listing(product_id=2, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L2"),
        ]
    )
    await session.commit()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    monkeypatch.setattr(listing_reconcile, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()
    yield
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()


async def _units(session, extra=None):
    query = select(Listing.product_id, Listing.variant_id).where(current_unit_filter())
    return {(p, v) for p, v in (await session.execute(query)).all()}


async def test_filter_keeps_exactly_the_units_a_sync_check_covers(session, variant_product):
    assert await _units(session) == {(1, 10), (2, None)}


async def test_reconcile_sweep_skips_superseded_rows(session, variant_product):
    picked = await listing_reconcile._listings_to_check(session, ListingPlatform.etsy, datetime.now(timezone.utc))

    assert {(l.product_id, l.variant_id) for l in picked} == {(1, 10), (2, None)}


class _RecordingAdapter:
    def __init__(self):
        self.pushed: list[tuple[str | None, int]] = []

    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        self.pushed.append((sku, qty))


@pytest.fixture
def adapter(monkeypatch):
    a = _RecordingAdapter()

    async def _get_adapter(session, platform):
        return a

    monkeypatch.setattr(listing_push, "get_adapter", _get_adapter)

    async def _resolve(session, product_id, variant_id):
        return 5

    monkeypatch.setattr(listing_push, "_resolve_max_sellable", _resolve)
    return a


async def test_product_level_enqueue_on_a_variant_product_pushes_nothing(session, variant_product, adapter, connection):
    """A product-level stock change on a variant product enqueues (product_id, None) —
    routers/products.update_product does exactly this on a ceiling edit. The row it finds
    is the pre-variant link, and pushing it sends the parent SKU to a listing that only
    carries the variant SKUs."""
    await listing_push._push_now(session, 1, None)

    assert adapter.pushed == []


async def test_deactivated_variant_is_not_pushed(session, variant_product, adapter, connection):
    await listing_push._push_now(session, 1, 11)

    assert adapter.pushed == []


async def test_current_units_still_push(session, variant_product, adapter, connection):
    await listing_push._push_now(session, 1, 10)
    await listing_push._push_now(session, 2, None)

    assert adapter.pushed == [("SKU-1-STD", 5), ("SKU-2", 5)]


async def test_push_corrections_ignores_superseded_rows(session, variant_product, adapter, connection):
    pushed, errors = await listing_push.push_units_now(session, 1, [None, 10, 11])

    assert (pushed, errors) == (1, [])
    assert adapter.pushed == [("SKU-1-STD", 5)]


async def test_sync_check_unlinks_superseded_rows_but_keeps_current_ones(session, variant_product):
    index = {
        "SKU-1-STD": ExternalListingRef(
            external_listing_id="L1", title="Hanger", sku="SKU-1-STD", state="active", quantity=5, variation="Std"
        )
    }

    summary = await listing_sync.check_product_sku_sync(session, 1, index, ListingPlatform.etsy)

    assert [(u.variant_id, u.status.value) for u in summary.units] == [(10, "synced")]
    rows = {
        l.variant_id: l
        for l in (await session.execute(select(Listing).where(Listing.product_id == 1))).scalars()
    }
    assert rows[10].external_listing_id == "L1"
    assert rows[None].external_listing_id is None
    assert rows[11].external_listing_id is None
    # The check happened, and the row records that — not "never tested".
    assert rows[None].last_checked_at is not None


async def test_failing_count_ignores_superseded_and_unlinked_units(session, variant_product):
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            # The orphaned product-level row: latest attempt failed, and it always will.
            PlatformListingPush(
                product_id=1, variant_id=None, platform=ListingPlatform.etsy, attempted_qty=5,
                status=ListingPushStatus.error, error_message="No matching SKU", attempted_at=now,
            ),
            # A current unit whose latest attempt failed — this one is real.
            PlatformListingPush(
                product_id=2, variant_id=None, platform=ListingPlatform.etsy, attempted_qty=5,
                status=ListingPushStatus.error, error_message="timeout", attempted_at=now,
            ),
        ]
    )
    await session.commit()

    failing, blocked = await _push_problem_counts(session)

    assert failing == {ListingPlatform.etsy: 1}
    assert blocked == {}


async def test_failing_count_drops_once_the_listing_is_unlinked(session, variant_product):
    """A sync check that finds no SKU clears external_listing_id and nothing retries the
    push afterwards, so 'the next stock change retries automatically' would be untrue."""
    session.add(
        PlatformListingPush(
            product_id=2, variant_id=None, platform=ListingPlatform.etsy, attempted_qty=5,
            status=ListingPushStatus.error, error_message="gone", attempted_at=datetime.now(timezone.utc),
        )
    )
    row = (
        await session.execute(select(Listing).where(Listing.product_id == 2, Listing.variant_id.is_(None)))
    ).scalar_one()
    row.external_listing_id = None
    await session.commit()

    failing, _ = await _push_problem_counts(session)

    assert failing == {}
