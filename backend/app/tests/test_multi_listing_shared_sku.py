"""Several Etsy listings sharing one SKU (A/B tests, a free-shipping variant) over one stock
pool.

The property that matters is overselling: a quantity push has to reach every listing
holding the SKU. Before this, the sync index let the last-crawled listing overwrite the rest
and the unit had room for one listing, so the others kept whatever quantity they last had
after stock hit zero.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.listing import Listing, ListingPlatform
from app.models.platform_listing_push import ListingPushStatus, PlatformListingPush
from app.models.product import Product
from app.services import listing_push, listing_reconcile, listing_sync, platform_api_usage, sync_status
from app.services.platforms.base import ExternalListingRef
from app.services.platforms.errors import PlatformPushBlockedError
from app.services.platforms.etsy import EtsyAdapter


def _ref(listing_id: str, *, sku: str = "SKU-1", qty: int = 5, state: str = "active") -> ExternalListingRef:
    return ExternalListingRef(
        external_listing_id=listing_id, title=f"Listing {listing_id}", sku=sku, state=state, quantity=qty, variation=None
    )


async def _check(session, multi, **kwargs):
    index, extras = listing_sync.split_multi_index(multi)
    return await listing_sync.check_product_sku_sync(
        session, 1, index, ListingPlatform.etsy, extra_index=extras, **kwargs
    )


async def _rows(session) -> list[Listing]:
    return list((await session.execute(select(Listing).order_by(Listing.id))).scalars())


@pytest.fixture
async def product(session):
    session.add(Product(id=1, name="Widget", sku="SKU-1"))
    await session.commit()


@pytest.fixture(autouse=True)
def _budget(monkeypatch, session_factory):
    monkeypatch.setattr(platform_api_usage, "async_session_factory", session_factory)
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()
    yield
    platform_api_usage._reset_for_tests()
    listing_push._deferred.clear()


# --- Discovery ------------------------------------------------------------------------


def test_every_listing_holding_a_sku_is_kept_by_the_etsy_index():
    def listing(listing_id, state="active"):
        return {
            "listing_id": listing_id,
            "title": f"T{listing_id}",
            "state": state,
            "inventory": {"products": [{"sku": "SKU-1", "offerings": [{"quantity": 3, "is_enabled": True}]}]},
        }

    multi: dict = {}
    for item in (listing(1), listing(2)):
        for ref in EtsyAdapter._listing_sku_refs(item):
            multi.setdefault(ref.sku, []).append(ref)

    assert [r.external_listing_id for r in multi["SKU-1"]] == ["1", "2"]
    # The single-listing index keeps its last-wins behaviour for callers that still use it.
    single: dict = {}
    for item in (listing(1), listing(2)):
        EtsyAdapter._index_listing_skus(item, single)
    assert single["SKU-1"].external_listing_id == "2"


def test_split_prefers_an_active_listing_and_treats_repeats_as_one_listing():
    index, extras = listing_sync.split_multi_index(
        {"SKU-1": [_ref("A", state="inactive"), _ref("B"), _ref("B"), _ref("C")]}
    )

    assert index["SKU-1"].external_listing_id == "B"
    assert [r.external_listing_id for r in extras["SKU-1"]] == ["C", "A"]


async def test_check_links_a_primary_and_an_additional_row(session, product):
    summary = await _check(session, {"SKU-1": [_ref("A", qty=5), _ref("B", qty=2)]})

    rows = await _rows(session)
    assert [(r.external_listing_id, r.is_primary) for r in rows] == [("A", True), ("B", False)]
    unit = summary.units[0]
    assert unit.external_listing_id == "A"
    assert [a.external_listing_id for a in unit.additional_listings] == ["B"]
    assert unit.additional_listings[0].listing_id == rows[1].id


async def test_a_drifted_additional_listing_flags_the_unit(session, product, monkeypatch):
    async def _resolve(session, product_id, variant_id):
        return 5

    monkeypatch.setattr(listing_push, "resolve_push_quantity", _resolve)

    summary = await _check(session, {"SKU-1": [_ref("A", qty=5), _ref("B", qty=2)]}, with_expected_quantity=True)

    unit = summary.units[0]
    assert unit.additional_listings[0].quantity_mismatch is True
    assert unit.quantity_mismatch is True  # the primary alone is fine; one push fixes both


async def test_the_primary_stays_put_when_crawl_order_changes(session, product):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    await _check(session, {"SKU-1": [_ref("B"), _ref("A")]})

    rows = await _rows(session)
    assert [(r.external_listing_id, r.is_primary) for r in rows] == [("A", True), ("B", False)]


async def test_an_additional_row_is_removed_when_its_listing_drops_the_sku(session, product):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    await _check(session, {"SKU-1": [_ref("A")]})

    assert [(r.external_listing_id, r.is_primary) for r in await _rows(session)] == [("A", True)]


async def test_a_surviving_additional_listing_is_promoted_when_the_primary_goes(session, product):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    await _check(session, {"SKU-1": [_ref("B")]})

    assert [(r.external_listing_id, r.is_primary) for r in await _rows(session)] == [("B", True)]


async def test_a_single_listing_behaves_exactly_as_before(session, product):
    summary = await _check(session, {"SKU-1": [_ref("A")]})

    assert [(r.external_listing_id, r.is_primary) for r in await _rows(session)] == [("A", True)]
    assert summary.units[0].additional_listings == []


# --- Push -----------------------------------------------------------------------------


class _Adapter:
    def __init__(self, blocked: set[str] | None = None):
        self.pushes: list[tuple[str, int]] = []
        self.blocked = blocked or set()

    async def push_listing_quantity(self, session, connection, listing_ref, sku, qty):
        if listing_ref.external_listing_id in self.blocked:
            raise PlatformPushBlockedError("quantity is not per SKU")
        self.pushes.append((listing_ref.external_listing_id, qty))


@pytest.fixture
def resolved(monkeypatch):
    async def _resolve(session, product_id, variant_id):
        return 0

    monkeypatch.setattr(listing_push, "_resolve_max_sellable", _resolve)


def _use(monkeypatch, adapter):
    async def _get_adapter(session, platform):
        return adapter

    monkeypatch.setattr(listing_push, "get_adapter", _get_adapter)


async def test_selling_out_reaches_every_listing_holding_the_sku(session, product, connection, resolved, monkeypatch):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    adapter = _Adapter()
    _use(monkeypatch, adapter)

    await listing_push._push_now(session, 1, None)

    assert sorted(adapter.pushes) == [("A", 0), ("B", 0)]


async def test_one_blocked_listing_does_not_hide_or_stop_the_other(
    session, product, connection, resolved, monkeypatch
):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    _use(monkeypatch, _Adapter(blocked={"A"}))

    pushed, errors = await listing_push.push_units_now(session, 1, [None])

    assert pushed == 1
    assert len(errors) == 1 and "quantity is not per SKU" in errors[0]
    # The primary's failure is worded as it always was; an additional one names its listing.
    _use(monkeypatch, _Adapter(blocked={"B"}))
    _, errors = await listing_push.push_units_now(session, 1, [None])
    assert "additional listing B" in errors[0]


async def test_push_attempts_are_logged_against_their_own_listing(session, product, connection, resolved, monkeypatch):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    _use(monkeypatch, _Adapter(blocked={"B"}))

    await listing_push.push_units_now(session, 1, [None])

    rows = {r.external_listing_id: r.id for r in await _rows(session)}
    logged = {
        p.listing_id: p.status for p in (await session.execute(select(PlatformListingPush))).scalars()
    }
    assert logged == {rows["A"]: ListingPushStatus.success, rows["B"]: ListingPushStatus.blocked}


async def test_reconcile_judges_each_listing_on_its_own_last_attempt(
    session, product, connection, resolved, monkeypatch
):
    """A sibling succeeding after a failure must not mark the failing listing as fine — the
    sweep would stop retrying it."""
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    _use(monkeypatch, _Adapter(blocked={"B"}))
    await listing_push.push_units_now(session, 1, [None])

    latest = await listing_reconcile._latest_attempt_status(session, ListingPlatform.etsy)

    rows = {r.external_listing_id: r.id for r in await _rows(session)}
    assert latest[(1, None, rows["A"])][0] == ListingPushStatus.success
    assert latest[(1, None, rows["B"])][0] == ListingPushStatus.blocked


async def test_the_failing_count_includes_a_failing_additional_listing(
    session, product, connection, resolved, monkeypatch
):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    _use(monkeypatch, _Adapter(blocked={"B"}))
    await listing_push.push_units_now(session, 1, [None])

    failing, blocked = await sync_status._push_problem_counts(session)

    assert blocked.get(ListingPlatform.etsy) == 1
    assert not failing


async def test_a_failure_on_a_removed_listing_stops_counting(session, product, connection, resolved, monkeypatch):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})
    _use(monkeypatch, _Adapter(blocked={"B"}))
    await listing_push.push_units_now(session, 1, [None])

    await _check(session, {"SKU-1": [_ref("A")]})  # B no longer holds the SKU

    failing, blocked = await sync_status._push_problem_counts(session)
    assert not failing and not blocked


async def test_stored_status_reads_back_the_additional_listings(session, product):
    await _check(session, {"SKU-1": [_ref("A"), _ref("B")]})

    summary = await listing_sync.get_stored_product_sync_status(session, 1, ListingPlatform.etsy)

    unit = summary.units[0]
    assert unit.external_listing_id == "A"
    assert [a.external_listing_id for a in unit.additional_listings] == ["B"]
