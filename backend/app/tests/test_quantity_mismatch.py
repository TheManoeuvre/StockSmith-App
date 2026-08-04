"""Tests for quantity-mismatch detection and the confirm-to-push correction flow.

The safety property under test is that a sync *check* never writes to a marketplace.
"Test sync" was read-only before this feature and must stay that way — correcting drift
is a separate, explicitly-confirmed action. A regression there would turn a diagnostic
button into one that silently mutates live listings.
"""

from datetime import datetime, timezone

import pytest

from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.schemas.listing import ListingSyncStatus
from app.services import listing_push, listing_sync
from app.services.listing_sync import _quantity_mismatch
from app.services.platforms.base import ExternalListingRef


def _ref(sku: str, quantity: int, state: str = "active") -> ExternalListingRef:
    return ExternalListingRef(
        external_listing_id=sku,
        title="A listing",
        sku=sku,
        state=state,
        quantity=quantity,
        variation=None,
    )


async def _seed_listing(session, *, external_quantity: int) -> None:
    """A product with one connected Etsy listing at the given marketplace quantity.
    Flushed in order because the test engine enforces foreign keys (see conftest)."""
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    session.add(PlatformConnection(platform=ListingPlatform.etsy, access_token="a", refresh_token="r"))
    await session.flush()
    session.add(
        Listing(
            product_id=1,
            variant_id=None,
            platform=ListingPlatform.etsy,
            external_listing_id="L1",
            external_state="active",
            external_quantity=external_quantity,
            last_checked_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


@pytest.fixture
def stub_push_quantity(monkeypatch):
    """Pins what listing_push would send, so these tests exercise the comparison rather
    than the whole buildability stack."""

    def _set(qty):
        async def _resolve(session, product_id, variant_id):
            return qty

        monkeypatch.setattr(listing_push, "resolve_push_quantity", _resolve)

    return _set


# --- The comparison itself ------------------------------------------------------------


def test_mismatch_requires_a_found_listing():
    """A SKU the marketplace doesn't have is a "not found" problem. Flagging it as a
    mismatch would offer to correct a listing that doesn't exist."""
    assert _quantity_mismatch(ListingSyncStatus.not_found, None, 5) is False
    assert _quantity_mismatch(ListingSyncStatus.not_tested, None, 5) is False
    # An inactive listing has a real quantity but pushing to it wouldn't make it sellable.
    assert _quantity_mismatch(ListingSyncStatus.listing_not_active, 2, 5) is False


def test_mismatch_requires_both_quantities():
    assert _quantity_mismatch(ListingSyncStatus.synced, None, 5) is False
    assert _quantity_mismatch(ListingSyncStatus.synced, 5, None) is False


def test_matching_quantities_are_not_a_mismatch():
    assert _quantity_mismatch(ListingSyncStatus.synced, 5, 5) is False


def test_zero_is_a_real_quantity_not_a_missing_one():
    """0 is the standard out-of-stock signal on both marketplaces, so a listing sitting at
    0 when StockSmith says 5 is exactly the drift worth catching — a falsy-check here
    would silently skip the most consequential case."""
    assert _quantity_mismatch(ListingSyncStatus.synced, 0, 5) is True
    assert _quantity_mismatch(ListingSyncStatus.synced, 5, 0) is True


# --- Through the sync check -----------------------------------------------------------


async def test_check_flags_a_drifted_quantity(session, stub_push_quantity):
    stub_push_quantity(7)
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.commit()

    summary = await listing_sync.check_product_sku_sync(
        session, 1, {"SKU-1": _ref("SKU-1", quantity=3)}, ListingPlatform.etsy, with_expected_quantity=True
    )

    unit = summary.units[0]
    assert unit.external_quantity == 3
    assert unit.expected_quantity == 7
    assert unit.quantity_mismatch is True


async def test_bulk_check_skips_the_quantity_computation(session, stub_push_quantity):
    """The shop-wide check reuses the per-product one; computing expected quantities there
    would turn one click into a buildability computation per unit in the catalogue."""
    stub_push_quantity(7)
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.commit()

    summary = await listing_sync.check_product_sku_sync(
        session, 1, {"SKU-1": _ref("SKU-1", quantity=3)}, ListingPlatform.etsy
    )

    unit = summary.units[0]
    assert unit.expected_quantity is None
    assert unit.quantity_mismatch is False


async def test_check_does_not_push(session, stub_push_quantity, monkeypatch):
    """The core safety property: a check reports drift, it never corrects it."""
    stub_push_quantity(7)
    pushed = []

    async def _explode(*args, **kwargs):
        pushed.append(args)
        raise AssertionError("check_product_sku_sync must never push to a marketplace")

    monkeypatch.setattr(listing_push, "push_units_now", _explode)
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.commit()

    await listing_sync.check_product_sku_sync(
        session, 1, {"SKU-1": _ref("SKU-1", quantity=3)}, ListingPlatform.etsy, with_expected_quantity=True
    )

    assert pushed == []


# --- The correction endpoint ----------------------------------------------------------


async def test_push_corrections_only_pushes_mismatched_units(session, stub_push_quantity, monkeypatch):
    """Re-derived from stored state, not taken from the client — a unit that has since
    been corrected must not be pushed again on a stale click."""
    import app.routers.platforms as platforms_router

    stub_push_quantity(7)
    await _seed_listing(session, external_quantity=7)  # already correct

    calls = []

    async def _record(session_, product_id, variant_ids):
        calls.append((product_id, variant_ids))
        return len(variant_ids), []

    monkeypatch.setattr(listing_push, "push_units_now", _record)

    result = await platforms_router.push_product_corrections(
        platform=ListingPlatform.etsy, product_id=1, session=session
    )

    assert calls == []  # nothing drifted, so nothing was pushed
    assert result.pushed_count == 0


async def test_push_corrections_reports_partial_failure(session, stub_push_quantity, monkeypatch):
    """3-of-4-corrected tells the user something a bare success/failure does not."""
    import app.routers.platforms as platforms_router

    stub_push_quantity(7)
    await _seed_listing(session, external_quantity=3)  # drifted

    async def _partial(session_, product_id, variant_ids):
        return 0, ["etsy listing for variant None: push failed"]

    monkeypatch.setattr(listing_push, "push_units_now", _partial)

    result = await platforms_router.push_product_corrections(
        platform=ListingPlatform.etsy, product_id=1, session=session
    )

    assert result.pushed_count == 0
    assert result.failed_count == 1
    assert "push failed" in result.errors[0]
