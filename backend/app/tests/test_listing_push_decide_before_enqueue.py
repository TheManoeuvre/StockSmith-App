"""Stage 5 of docs/plan-listing-push-rate-reduction.md — decide before you enqueue.

enqueue_for_material used to _enqueue every product/variant whose BOM references the
material: ~130 debounced asyncio tasks + DB sessions + buildability passes for one
ordinary stock movement on a shared material, almost none of which changed the number any
listing would actually be sent. Now one debounced job per material does a single batched
buildability pass, diffs each affected listing's resolved target against its
last_pushed_qty watermark, and only acts on the ones that genuinely moved.

These tests pin the "decide, don't fan out" behaviour: only-changed listings are acted
on, an all-unchanged tick does nothing, and the whole diff runs in one session / one
scheduled job per material. Directional routing of what *does* move (Stage 6) is covered
in test_listing_push_directional_cadence.py; here every acted-on move is downward so it
dispatches immediately.
"""

from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.kitting import ProductKittingMaterial
from app.models.listing import Listing, ListingPlatform
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.services import buildability, listing_push


@pytest_asyncio.fixture
async def catalogue(session):
    """One shared build material across three products (max_buildable 10 each), plus a
    generously-stocked packaging material so the resolved target reads as materials-
    limited rather than "at full capacity" — Stage 6's deadband override keys off that,
    and these Stage 5 tests want it out of the way."""
    build_mat = Material(
        name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g, current_qty=Decimal(100)
    )
    pack_mat = Material(
        name="Box", category=LegacyMaterialCategory.packaging, unit=MaterialUnit.each, current_qty=Decimal(9999)
    )
    session.add_all([build_mat, pack_mat])
    await session.flush()

    product_ids = []
    for i in range(1, 4):
        p = Product(name=f"P{i}", sku=f"SKU-{i}", push_buildable_capacity=True)
        session.add(p)
        await session.flush()
        session.add(ProductMaterial(product_id=p.id, material_id=build_mat.id, qty_required=Decimal(10)))
        session.add(ProductKittingMaterial(product_id=p.id, material_id=pack_mat.id, qty_required=Decimal(1)))
        product_ids.append(p.id)
    await session.commit()
    return {"material_id": build_mat.id, "product_ids": product_ids}


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch):
    monkeypatch.setattr(listing_push, "_DEBOUNCE_SECONDS", 0)
    listing_push._pending.clear()
    listing_push._pending_materials.clear()
    listing_push._reconcile_soon.clear()
    yield
    listing_push._pending.clear()
    listing_push._pending_materials.clear()
    listing_push._reconcile_soon.clear()


@pytest.fixture
def enqueued(monkeypatch):
    seen: list[tuple[int, int | None]] = []
    monkeypatch.setattr(listing_push, "_enqueue", lambda pid, vid: seen.append((pid, vid)))
    return seen


async def test_target_resolves_to_10(session, catalogue):
    # Guard: the fixture really does resolve to 10 for each product, so the watermarks
    # below mean what the test says they mean.
    assert await buildability.get_max_buildable_by_product(session) == {pid: 10 for pid in catalogue["product_ids"]}
    for pid in catalogue["product_ids"]:
        assert await listing_push.resolve_push_quantity(session, pid, None) == 10


async def test_only_the_listing_whose_target_moved_is_acted_on(session, catalogue, enqueued):
    p1, p2, p3 = catalogue["product_ids"]
    session.add_all(
        [
            # p1: marketplace already holds 10 -> unchanged, nothing to do
            Listing(product_id=p1, platform=ListingPlatform.etsy, external_listing_id="L1", last_pushed_qty=10,
                    last_pushed_at=_ago()),
            # p2: marketplace holds 40 -> target 10 is a downward move, dispatch now
            Listing(product_id=p2, platform=ListingPlatform.etsy, external_listing_id="L2", last_pushed_qty=40,
                    last_pushed_at=_ago()),
            # p3: never pushed -> get the first real number out now
            Listing(product_id=p3, platform=ListingPlatform.etsy, external_listing_id="L3", last_pushed_qty=None),
        ]
    )
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert sorted(enqueued) == sorted([(p2, None), (p3, None)])
    assert not listing_push._reconcile_soon


async def test_all_unchanged_tick_does_nothing(session, catalogue, enqueued):
    for pid in catalogue["product_ids"]:
        session.add(
            Listing(product_id=pid, platform=ListingPlatform.etsy, external_listing_id=f"L{pid}",
                    last_pushed_qty=10, last_pushed_at=_ago())
        )
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert enqueued == []
    assert not listing_push._reconcile_soon


async def test_structurally_blocked_listing_is_not_a_reason_to_act(session, catalogue, enqueued):
    p1 = catalogue["product_ids"][0]
    session.add(
        Listing(
            product_id=p1, platform=ListingPlatform.etsy, external_listing_id="L1",
            last_pushed_qty=3, last_pushed_at=_ago(), structural_push_block="quantity doesn't vary by variation",
        )
    )
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert enqueued == []
    assert not listing_push._reconcile_soon


async def test_one_scheduled_job_and_one_session_per_material_tick(
    session, catalogue, enqueued, monkeypatch, session_factory
):
    """A material tick spawns O(1) scheduling + one batched session, not one per affected
    product/variant."""
    for pid in catalogue["product_ids"]:
        session.add(
            Listing(product_id=pid, platform=ListingPlatform.etsy, external_listing_id=f"L{pid}",
                    last_pushed_qty=99, last_pushed_at=_ago())  # downward -> dispatched now
        )
    await session.commit()

    sessions_opened = 0

    def _counting_factory():
        nonlocal sessions_opened
        sessions_opened += 1
        return session_factory()

    monkeypatch.setattr(listing_push, "async_session_factory", _counting_factory)

    await listing_push.enqueue_for_material(session, catalogue["material_id"])
    await listing_push.enqueue_for_material(session, catalogue["material_id"])
    assert len(listing_push._pending_materials) == 1

    await listing_push._pending_materials[catalogue["material_id"]]

    assert sessions_opened == 1, "one batched session for the diff, not one per affected product"
    assert len(enqueued) == 3, "all three moved 99 -> 10, dispatched via the single diff job"


def _ago():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(hours=1)
