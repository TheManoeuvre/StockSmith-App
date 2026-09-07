"""Stage 1 rider of docs/plan-listing-push-rate-reduction.md (and the docs/backlog.md
entry "Etsy quantity pushes fail permanently when a listing's quantity doesn't vary by
variation").

An Etsy listing whose `quantity` isn't attached to a variation property forces every
variant to share one stock number — updateListingInventory 400s "quantity must be
consistent across all products" for any per-SKU value that isn't the shared one, and it
will 400 identically forever until the seller changes the listing. These tests pin:
  - the adapter detects it from the GET it already does and raises a distinct error;
  - listing_push persists a marker on the Listing instead of logging a retryable failure;
  - the fan-out, the reconcile sweep's normal selection, and the menu-bar badge all skip
    a marked listing;
  - a push that finally goes through clears the marker.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.services import listing_push, listing_reconcile, platform_api_usage, sync_status
from app.services.platforms.errors import PlatformListingStructuralError
from app.services.platforms.etsy import EtsyAdapter
from app.services.platforms.base import ExternalListingRef


# --- adapter detection --------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


def _inventory(*, products: int, quantity_on_property: list) -> dict:
    return {
        "products": [
            {
                "sku": f"SKU-{i}",
                "is_deleted": False,
                "property_values": [],
                "offerings": [
                    {"quantity": 3, "is_enabled": True, "readiness_state_id": 1, "price": {"amount": 500, "divisor": 100}}
                ],
            }
            for i in range(products)
        ],
        "price_on_property": [],
        "quantity_on_property": quantity_on_property,
        "sku_on_property": [],
    }


class _RecordingEtsy(EtsyAdapter):
    def __init__(self, inventory: dict):
        self._inventory = inventory
        self.calls: list[tuple[str, str]] = []

    async def _authed_request(self, session, connection, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "GET":
            return _FakeResponse(200, self._inventory)
        return _FakeResponse(200, {})


_REF = ExternalListingRef(
    external_listing_id="L1", title="t", sku="SKU-0", state="active", quantity=0, variation=None
)


async def test_adapter_raises_structural_error_when_quantity_does_not_vary():
    adapter = _RecordingEtsy(_inventory(products=3, quantity_on_property=[]))

    with pytest.raises(PlatformListingStructuralError) as exc:
        await adapter.push_listing_quantity(None, None, _REF, "SKU-0", 5)

    assert "doesn't vary by variation" in str(exc.value)
    assert ("PUT", "/listings/L1/inventory") not in adapter.calls, "must not attempt the doomed PUT"


async def test_adapter_does_not_raise_when_quantity_varies_by_property():
    adapter = _RecordingEtsy(_inventory(products=3, quantity_on_property=[513]))

    await adapter.push_listing_quantity(None, None, _REF, "SKU-0", 5)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


async def test_adapter_does_not_raise_for_a_single_product_listing():
    adapter = _RecordingEtsy(_inventory(products=1, quantity_on_property=[]))

    await adapter.push_listing_quantity(None, None, _REF, "SKU-0", 5)

    assert ("PUT", "/listings/L1/inventory") in adapter.calls


# --- listing_push marker lifecycle -------------------------------------------------


class _StructuralAdapter:
    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        raise PlatformListingStructuralError("this listing's quantity doesn't vary by variation — fix it on Etsy")


class _OkAdapter:
    def __init__(self):
        self.pushes: list[int] = []

    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        self.pushes.append(qty)


@pytest_asyncio.fixture
async def one_listing(session):
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.flush()
    listing = Listing(product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1")
    session.add(listing)
    await session.commit()
    return listing


@pytest.fixture
def _resolved(monkeypatch):
    async def _resolve(session, product_id, variant_id):
        return 7

    monkeypatch.setattr(listing_push, "_resolve_max_sellable", _resolve)


@pytest.fixture(autouse=True)
def _budget(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()
    yield
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()


def _use_adapter(monkeypatch, adapter):
    async def _get_adapter(session, platform):
        return adapter

    monkeypatch.setattr(listing_push, "get_adapter", _get_adapter)


async def test_push_one_persists_the_marker_and_writes_no_error_row(session, one_listing, connection, monkeypatch):
    _use_adapter(monkeypatch, _StructuralAdapter())

    await listing_push._push_one(session, one_listing, 7)

    await session.refresh(one_listing)
    assert one_listing.structural_push_block is not None
    assert one_listing.structural_push_block_at is not None
    rows = (await session.execute(_all_pushes())).scalars().all()
    assert rows == [], "a structural block is a standing config problem, not a retryable attempt"


async def test_push_one_success_clears_an_existing_marker(session, one_listing, connection, monkeypatch):
    one_listing.structural_push_block = "old block"
    one_listing.structural_push_block_at = datetime.now(timezone.utc) - timedelta(days=2)
    await session.commit()
    ok = _OkAdapter()
    _use_adapter(monkeypatch, ok)

    await listing_push._push_one(session, one_listing, 7)

    await session.refresh(one_listing)
    assert one_listing.structural_push_block is None
    assert one_listing.structural_push_block_at is None
    assert ok.pushes == [7]


async def test_push_now_skips_a_marked_listing(session, one_listing, connection, _resolved, monkeypatch):
    one_listing.structural_push_block = "quantity doesn't vary by variation"
    one_listing.structural_push_block_at = datetime.now(timezone.utc)
    await session.commit()
    ok = _OkAdapter()
    _use_adapter(monkeypatch, ok)

    await listing_push._push_now(session, 1, None)

    assert ok.pushes == [], "no GET, no PUT for a structurally-blocked listing"


# --- reconcile sweep -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reconcile_wiring(monkeypatch, session_factory):
    monkeypatch.setattr(listing_reconcile, "async_session_factory", session_factory)


async def test_listings_to_check_excludes_marked_listings(session, one_listing):
    one_listing.structural_push_block = "blocked"
    one_listing.structural_push_block_at = datetime.now(timezone.utc)
    await session.commit()

    cutoff = datetime.now(timezone.utc) - listing_reconcile._STALE_AFTER
    picked = await listing_reconcile._listings_to_check(session, ListingPlatform.etsy, cutoff)

    assert picked == []


async def test_marked_to_reprobe_returns_only_stale_marks(session, connection):
    session.add(Product(id=1, name="P1", sku="S1"))
    session.add(Product(id=2, name="P2", sku="S2"))
    await session.flush()
    now = datetime.now(timezone.utc)
    session.add(
        Listing(
            product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1",
            structural_push_block="b", structural_push_block_at=now - timedelta(hours=30),
        )
    )
    session.add(
        Listing(
            product_id=2, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L2",
            structural_push_block="b", structural_push_block_at=now - timedelta(hours=1),
        )
    )
    await session.commit()

    picked = await listing_reconcile._marked_to_reprobe(session, ListingPlatform.etsy, now)

    assert [l.product_id for l in picked] == [1], "only the mark older than the re-probe window"


async def test_reconcile_platform_reprobes_stale_marked_listings(session, connection, monkeypatch):
    session.add(Product(id=1, name="P1", sku="S1"))
    await session.flush()
    session.add(
        Listing(
            product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1",
            last_pushed_at=datetime.now(timezone.utc),  # fresh -> not in the normal selection
            structural_push_block="b", structural_push_block_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
    )
    await session.commit()

    seen: list[int] = []

    async def _reconcile_listing(s, listing):
        seen.append(listing.product_id)

    monkeypatch.setattr(listing_push, "reconcile_listing", _reconcile_listing)

    await listing_reconcile._reconcile_platform(ListingPlatform.etsy)

    assert seen == [1]


# --- menu-bar badge -------------------------------------------------------------


async def test_failing_push_counts_ignores_marked_listings_and_counts_them_separately(session, connection):
    session.add(Product(id=1, name="P1", sku="S1"))
    await session.flush()
    session.add(
        Listing(
            product_id=1, variant_id=None, platform=ListingPlatform.etsy, external_listing_id="L1",
            structural_push_block="quantity doesn't vary by variation",
            structural_push_block_at=datetime.now(timezone.utc),
        )
    )
    # A leftover error row from before the block was detected — the exact thing the badge
    # used to keep counting.
    session.add(
        PlatformListingPush(
            product_id=1, variant_id=None, platform=ListingPlatform.etsy,
            attempted_qty=3, status=ListingPushStatus.error, error_message="quantity must be consistent",
        )
    )
    await session.commit()

    failing = await sync_status._failing_push_counts(session)
    structural = await sync_status._structurally_unpushable_counts(session)

    assert failing.get(ListingPlatform.etsy, 0) == 0
    assert structural[ListingPlatform.etsy] == 1

    summary = {s.platform: s for s in await sync_status.get_sync_summary(session)}
    assert summary[ListingPlatform.etsy].failing_push_count == 0
    assert summary[ListingPlatform.etsy].structurally_unpushable_count == 1


# --- helpers ------------------------------------------------------------------------


def _all_pushes():
    from sqlalchemy import select

    return select(PlatformListingPush)
