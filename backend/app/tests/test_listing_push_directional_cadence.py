"""Stage 6 of docs/plan-listing-push-rate-reduction.md — directional cadence + deadband.

Split each pending quantity move by direction against the last-pushed watermark:
  - target < last_pushed (oversell risk): dispatch now, seconds-scale debounce.
  - target > last_pushed (upside only): route onto listing_reconcile's hourly sweep.
  - upward, but a sub-deadband nudge: drop it entirely (the sweep re-asserts eventually).

The deadband is upward-only and compares against the fully resolved push quantity; a
downward move is never deadbanded, and an upward move back to full resolved capacity
(nothing constraining it, or back at the platform ceiling) overrides the deadband.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.kitting import ProductKittingMaterial
from app.models.listing import Listing, ListingPlatform
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.platform_connection import PlatformConnection
from app.models.product import Product, ProductMaterial
from app.services import listing_push, listing_reconcile, platform_api_usage


# --- pure helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    "last, target, at_cap, expected",
    [
        (100, 100, False, False),   # no move
        (100, 105, False, False),   # +5% — sub-deadband
        (100, 110, False, True),    # +10% — clears the deadband
        (100, 103, True, True),     # tiny move, but back at full capacity
        (0, 3, False, True),        # back in stock from zero — fraction test can't apply
        (100, 101, False, False),   # +1 but only 1%
    ],
)
def test_upward_is_material(last, target, at_cap, expected):
    assert listing_push._upward_is_material(last, target, at_cap) is expected


def _listing(last_pushed, *, at=..., blocked=None):
    lst = Listing(product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1")
    lst.last_pushed_qty = last_pushed
    lst.last_pushed_at = (datetime.now(timezone.utc) - timedelta(hours=1)) if at is ... else at
    lst.structural_push_block = blocked
    return lst


@pytest.mark.parametrize(
    "listings, target, at_cap, expected",
    [
        ([_listing(40)], 10, False, "now"),                 # downward
        ([_listing(None)], 10, False, "now"),               # never pushed
        ([_listing(10, at=None)], 10, False, "now"),        # qty set but never confirmed
        ([_listing(100)], 110, False, "sweep"),             # material upward
        ([_listing(100)], 103, False, "skip"),              # sub-deadband upward
        ([_listing(100)], 103, True, "sweep"),              # sub-deadband, but at capacity
        ([_listing(400), _listing(100)], 103, False, "now"), # one listing down, one up -> down wins
    ],
)
def test_direction_for(listings, target, at_cap, expected):
    assert listing_push._direction_for(listings, target, at_cap) == expected


def test_take_reconcile_soon_drains():
    listing_push._reconcile_soon.update({(1, None), (2, 3)})
    drained = listing_push.take_reconcile_soon()
    assert sorted(drained) == [(1, None), (2, 3)]
    assert not listing_push._reconcile_soon


# --- integration through the material diff --------------------------------------


@pytest_asyncio.fixture
async def one_product(session):
    """Resolved push target 100 (build_mat 1000g / 10g per unit), plus a well-stocked
    packaging BOM so the target reads as materials-limited — not "at full capacity" — and
    the deadband fraction is what decides."""
    build_mat = Material(
        name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g, current_qty=Decimal(1000)
    )
    pack_mat = Material(
        name="Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, current_qty=Decimal(9999)
    )
    session.add_all([build_mat, pack_mat])
    await session.flush()
    p = Product(name="P1", sku="SKU-1", push_buildable_capacity=True)
    session.add(p)
    await session.flush()
    session.add(ProductMaterial(product_id=p.id, material_id=build_mat.id, qty_required=Decimal(10)))
    session.add(ProductKittingMaterial(product_id=p.id, material_id=pack_mat.id, qty_required=Decimal(1)))
    await session.commit()
    return {"material_id": build_mat.id, "product_id": p.id}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    monkeypatch.setattr(listing_reconcile, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    listing_push._pending.clear()
    listing_push._pending_materials.clear()
    listing_push._reconcile_soon.clear()
    yield
    platform_api_usage._reset_for_tests()
    listing_push._pending.clear()
    listing_push._pending_materials.clear()
    listing_push._reconcile_soon.clear()


@pytest.fixture
def enqueued(monkeypatch):
    seen: list[tuple[int, int | None]] = []
    monkeypatch.setattr(listing_push, "_enqueue", lambda pid, vid: seen.append((pid, vid)))
    return seen


async def _run(session, one_product):
    await listing_push._diff_and_enqueue_material(session, one_product["material_id"])


async def test_downward_move_dispatches_now(session, one_product, enqueued):
    session.add(_row(one_product, 400))  # target 100 -> downward
    await session.commit()
    await _run(session, one_product)
    assert enqueued == [(one_product["product_id"], None)]
    assert not listing_push._reconcile_soon


async def test_material_upward_move_routes_to_the_sweep(session, one_product, enqueued):
    session.add(_row(one_product, 3))  # 3 -> 100: upward, well past the deadband
    await session.commit()
    await _run(session, one_product)
    assert enqueued == []
    assert listing_push._reconcile_soon == {(one_product["product_id"], None)}


async def test_sub_deadband_upward_move_is_dropped(session, one_product, enqueued):
    session.add(_row(one_product, 95))  # 95 -> 100: +5, ~5.3% of 95, and not at capacity
    await session.commit()
    await _run(session, one_product)
    assert enqueued == []
    assert not listing_push._reconcile_soon, "a sub-deadband upward nudge is dropped outright"


async def test_reconcile_sweep_picks_up_routed_keys(session, one_product, monkeypatch):
    # A connected platform is required for _reconcile_platform to do anything.
    session.add(
        PlatformConnection(
            platform=ListingPlatform.etsy, access_token="t", refresh_token="r",
            access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_account_id="1", auto_sync_enabled=True,
        )
    )
    session.add(_row(one_product, 3, at=datetime.now(timezone.utc)))  # fresh -> not in the stale set
    await session.commit()

    # Route it (upward move).
    await listing_push._diff_and_enqueue_material(session, one_product["material_id"])
    assert listing_push._reconcile_soon == {(one_product["product_id"], None)}

    seen: list[int] = []

    async def _reconcile_listing(s, listing):
        seen.append(listing.product_id)

    monkeypatch.setattr(listing_push, "reconcile_listing", _reconcile_listing)

    await listing_reconcile._tick()

    assert seen == [one_product["product_id"]]
    assert not listing_push._reconcile_soon, "drained by the tick"


def _row(one_product, last_pushed, *, at=None):
    return Listing(
        product_id=one_product["product_id"],
        variant_id=None,
        platform=ListingPlatform.etsy,
        external_listing_id="L1",
        last_pushed_qty=last_pushed,
        last_pushed_at=at or (datetime.now(timezone.utc) - timedelta(hours=1)),
    )
