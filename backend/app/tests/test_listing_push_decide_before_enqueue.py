"""Stage 5 of docs/plan-listing-push-rate-reduction.md — decide before you enqueue.

enqueue_for_material used to _enqueue every product/variant whose BOM references the
material: ~130 debounced asyncio tasks + DB sessions + buildability passes for one
ordinary stock movement on a shared material, almost none of which changed the number any
listing would actually be sent. Now one debounced job per material does a single batched
buildability pass, diffs each affected listing's resolved target against its
last_pushed_qty watermark, and only _enqueue()s the ones that genuinely moved.

These tests pin: only-changed listings are enqueued, an all-unchanged tick enqueues
nothing, and the whole diff runs in one session / one scheduled job per material.
"""

from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.listing import Listing, ListingPlatform
from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.product import Product, ProductMaterial
from app.services import buildability, listing_push


@pytest_asyncio.fixture
async def catalogue(session):
    """One shared material in three products' build BOMs. current_qty 100, qty_required 10
    -> max_buildable 10 for each; no variants, no packaging BOM, so the resolved push
    target for each bare product is 10."""
    mat = Material(name="PLA", category=LegacyMaterialCategory.filament, unit=MaterialUnit.g, current_qty=Decimal(100))
    session.add(mat)
    await session.flush()

    products = []
    for i in range(1, 4):
        p = Product(name=f"P{i}", sku=f"SKU-{i}", push_buildable_capacity=True)
        session.add(p)
        await session.flush()
        session.add(ProductMaterial(product_id=p.id, material_id=mat.id, qty_required=Decimal(10)))
        products.append(p)
    await session.commit()
    return {"material_id": mat.id, "product_ids": [p.id for p in products]}


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch):
    monkeypatch.setattr(listing_push, "_DEBOUNCE_SECONDS", 0)
    listing_push._pending.clear()
    listing_push._pending_materials.clear()
    yield
    listing_push._pending.clear()
    listing_push._pending_materials.clear()


@pytest.fixture
def enqueued(monkeypatch):
    seen: list[tuple[int, int | None]] = []
    monkeypatch.setattr(listing_push, "_enqueue", lambda pid, vid: seen.append((pid, vid)))
    return seen


async def test_target_confirms_10(session, catalogue):
    # Guard: the fixture really does resolve to 10 for each product, so the watermarks
    # below mean what the test says they mean.
    assert await buildability.get_max_buildable_by_product(session) == {pid: 10 for pid in catalogue["product_ids"]}
    for pid in catalogue["product_ids"]:
        assert await listing_push.resolve_push_quantity(session, pid, None) == 10


async def test_only_the_listing_whose_target_moved_is_enqueued(session, catalogue, enqueued):
    p1, p2, p3 = catalogue["product_ids"]
    session.add_all(
        [
            # p1: marketplace already holds 10 -> unchanged, must not enqueue
            Listing(product_id=p1, platform=ListingPlatform.etsy, external_listing_id="L1", last_pushed_qty=10),
            # p2: marketplace holds 3 -> changed, must enqueue
            Listing(product_id=p2, platform=ListingPlatform.etsy, external_listing_id="L2", last_pushed_qty=3),
            # p3: never pushed -> changed (None != 10), must enqueue
            Listing(product_id=p3, platform=ListingPlatform.etsy, external_listing_id="L3", last_pushed_qty=None),
        ]
    )
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert sorted(enqueued) == sorted([(p2, None), (p3, None)])


async def test_all_unchanged_tick_enqueues_nothing(session, catalogue, enqueued):
    for pid in catalogue["product_ids"]:
        session.add(Listing(product_id=pid, platform=ListingPlatform.etsy, external_listing_id=f"L{pid}", last_pushed_qty=10))
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert enqueued == []


async def test_structurally_blocked_listing_is_not_a_reason_to_enqueue(session, catalogue, enqueued):
    p1 = catalogue["product_ids"][0]
    session.add(
        Listing(
            product_id=p1, platform=ListingPlatform.etsy, external_listing_id="L1",
            last_pushed_qty=3, structural_push_block="quantity doesn't vary by variation",
        )
    )
    await session.commit()

    await listing_push._diff_and_enqueue_material(session, catalogue["material_id"])

    assert enqueued == [], "a listing that can't be pushed until the user fixes it isn't work to schedule"


async def test_one_scheduled_job_and_one_session_per_material_tick(session, catalogue, enqueued, monkeypatch, session_factory):
    """The whole point of Stage 5: a material tick spawns O(1) scheduling + one batched
    session, not one per affected product/variant."""
    for pid in catalogue["product_ids"]:
        session.add(Listing(product_id=pid, platform=ListingPlatform.etsy, external_listing_id=f"L{pid}", last_pushed_qty=1))
    await session.commit()

    sessions_opened = 0
    real_factory = session_factory

    def _counting_factory():
        nonlocal sessions_opened
        sessions_opened += 1
        return real_factory()

    monkeypatch.setattr(listing_push, "async_session_factory", _counting_factory)

    # Two enqueues for the same material coalesce into one scheduled job.
    await listing_push.enqueue_for_material(session, catalogue["material_id"])
    await listing_push.enqueue_for_material(session, catalogue["material_id"])
    assert len(listing_push._pending_materials) == 1

    await listing_push._pending_materials[catalogue["material_id"]]

    assert sessions_opened == 1, "one batched session for the diff, not one per affected product"
    # All three moved 1 -> 10, so all three are enqueued — but via the single diff job.
    assert len(enqueued) == 3
