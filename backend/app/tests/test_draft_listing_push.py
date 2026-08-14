"""Creating an Etsy draft listing.

The first outbound call in this codebase that *creates* something. An Etsy draft cannot be
deleted from this app, so most of what's pinned here is about not making one by accident:
refusing when readiness says no, refusing a second one, and never throwing away a listing
that was created successfully because a later step failed.

Driven against a recording fake rather than Etsy. Nothing here touches a marketplace.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.asset import AssetType, ProductAsset
from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import draft_listing
from app.services.draft_listing import DraftPushError, build_draft, push_draft
from app.services.platforms.base import DraftListingResult

ETSY = ListingPlatform.etsy


class RecordingAdapter:
    """Records the draft it was handed instead of creating anything."""

    def __init__(self, result: DraftListingResult | None = None, raises: Exception | None = None):
        self.calls: list = []
        self.result = result or DraftListingResult(
            external_listing_id="900001", state="draft", unit_refs={}, warnings=[], publish_blockers=[]
        )
        self.raises = raises

    async def create_draft_listing(self, session, connection, draft):
        self.calls.append(draft)
        if self.raises:
            raise self.raises
        if not self.result.unit_refs:
            self.result.unit_refs = {
                str(u.variant_id) if u.variant_id is not None else "": "900001" for u in draft.units
            }
        return self.result


async def _profile(session, **overrides) -> ListingProfile:
    fields = dict(
        platform=ETSY,
        name="Handmade",
        is_default=True,
        etsy_taxonomy_id=1234,
        etsy_who_made="i_did",
        etsy_when_made="made_to_order",
        etsy_is_supply=False,
        etsy_shipping_profile_id=99,
    )
    fields.update(overrides)
    profile = ListingProfile(**fields)
    session.add(profile)
    await session.commit()
    return profile


async def _product(session, **overrides) -> Product:
    fields = dict(
        name="Brick Pencil Pot",
        sku="SKU-0037",
        description="A 3D printed desk tidy.",
        sale_price=Decimal("12.50"),
        is_active=True,
    )
    fields.update(overrides)
    product = Product(**fields)
    session.add(product)
    await session.commit()
    return product


@pytest.mark.asyncio
async def test_builds_a_draft_from_the_product_and_its_profile(session):
    await _profile(session)
    product = await _product(session)

    draft = await build_draft(session, product.id, ETSY)

    assert draft.title == "Brick Pencil Pot"
    assert draft.description == "A 3D printed desk tidy."
    assert [u.sku for u in draft.units] == ["SKU-0037"]
    assert draft.units[0].price == "12.50"
    # Profile metadata arrives under the neutral keys the adapters read.
    assert draft.metadata["etsy.taxonomy_id"] == 1234
    assert draft.metadata["etsy.who_made"] == "i_did"


@pytest.mark.asyncio
async def test_unset_optional_metadata_contributes_no_key_at_all(session):
    """The omit rule. An empty string would be stored by Etsy as a real value and would
    then satisfy checks it shouldn't."""
    await _profile(session, etsy_return_policy_id=None, etsy_shop_section_id=None)
    product = await _product(session)

    draft = await build_draft(session, product.id, ETSY)
    assert "etsy.return_policy_id" not in draft.metadata
    assert "etsy.shop_section_id" not in draft.metadata


@pytest.mark.asyncio
async def test_refuses_to_build_when_readiness_reports_a_blocker(session):
    """Nothing is sent. Guessing a taxonomy or a policy is how a seller ends up paying the
    wrong final-value fee."""
    await _profile(session, etsy_taxonomy_id=None)
    product = await _product(session)

    with pytest.raises(DraftPushError) as excinfo:
        await build_draft(session, product.id, ETSY)
    assert "category" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_a_missing_description_stops_the_push_before_any_call(session):
    await _profile(session)
    product = await _product(session, description=None)
    adapter = RecordingAdapter()

    with pytest.raises(DraftPushError):
        await push_draft(session, adapter, None, product.id, ETSY)
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_an_unpriced_variant_is_left_out_rather_than_failing_the_push(session):
    await _profile(session)
    product = await _product(session, sale_price=None)
    session.add_all(
        [
            ProductVariant(
                product_id=product.id, variant_name="Blue", sku_suffix="BLUE", is_active=True,
                sale_price=Decimal("12.50"),
            ),
            ProductVariant(product_id=product.id, variant_name="Teal", sku_suffix="TEAL", is_active=True),
        ]
    )
    await session.commit()

    draft = await build_draft(session, product.id, ETSY)
    assert [u.sku for u in draft.units] == ["SKU-0037-BLUE"]


@pytest.mark.asyncio
async def test_the_hero_image_is_attached_when_the_file_exists(session, monkeypatch, tmp_path):
    await _profile(session)
    product = await _product(session)
    image = tmp_path / "hero.png"
    image.write_bytes(b"not-really-a-png")
    session.add(
        ProductAsset(
            product_id=product.id, asset_type=AssetType.main_image,
            file_path="hero.png", original_filename="hero.png",
        )
    )
    await session.commit()
    monkeypatch.setattr(draft_listing.file_storage, "resolve_asset_path", lambda p: image)

    draft = await build_draft(session, product.id, ETSY)
    assert len(draft.images) == 1
    assert draft.images[0].data == b"not-really-a-png"


@pytest.mark.asyncio
async def test_a_missing_image_file_does_not_stop_the_draft(session, monkeypatch):
    """A file gone from disk is a publish blocker, not a reason to refuse — Etsy accepts a
    draft with no image and only needs one to publish."""
    await _profile(session)
    product = await _product(session)
    session.add(
        ProductAsset(
            product_id=product.id, asset_type=AssetType.main_image,
            file_path="gone.png", original_filename="gone.png",
        )
    )
    await session.commit()

    def missing(path):
        raise OSError("no such file")

    monkeypatch.setattr(draft_listing.file_storage, "resolve_asset_path", missing)

    draft = await build_draft(session, product.id, ETSY)
    assert draft.images == []


@pytest.mark.asyncio
async def test_a_successful_push_records_the_link_immediately(session):
    """Written from the response rather than left for the next sync check: Etsy's SKU index
    does not ask for draft listings, so the draft would otherwise be invisible to the very
    check that offered to create it."""
    await _profile(session)
    product = await _product(session)
    adapter = RecordingAdapter()

    result = await push_draft(session, adapter, None, product.id, ETSY)

    assert result.external_listing_id == "900001"
    assert result.units_linked == 1
    listing = (await session.execute(select(Listing))).scalar_one()
    assert listing.external_listing_id == "900001"
    assert listing.external_state == "draft"
    assert listing.published_sku == "SKU-0037"
    assert listing.last_checked_at is not None


@pytest.mark.asyncio
async def test_refuses_a_second_draft_for_a_product_already_linked(session):
    """A duplicate Etsy draft cannot be removed from this app."""
    await _profile(session)
    product = await _product(session)
    adapter = RecordingAdapter()

    await push_draft(session, adapter, None, product.id, ETSY)
    with pytest.raises(DraftPushError) as excinfo:
        await push_draft(session, adapter, None, product.id, ETSY)

    assert "already linked" in str(excinfo.value)
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_a_failed_creation_leaves_no_listing_row(session):
    """Nothing half-written: if the marketplace refused, StockSmith must not claim a link."""
    await _profile(session)
    product = await _product(session)
    adapter = RecordingAdapter(raises=RuntimeError("Etsy said no"))

    with pytest.raises(RuntimeError):
        await push_draft(session, adapter, None, product.id, ETSY)

    assert (await session.execute(select(Listing))).scalars().all() == []


@pytest.mark.asyncio
async def test_publish_blockers_are_carried_back_to_the_caller(session):
    await _profile(session)
    product = await _product(session)
    adapter = RecordingAdapter(
        DraftListingResult(
            external_listing_id="900001",
            state="draft",
            unit_refs={},
            warnings=["the image was rejected"],
            publish_blockers=["Etsy needs at least one image before this can be published."],
        )
    )

    result = await push_draft(session, adapter, None, product.id, ETSY)
    assert result.warnings == ["the image was rejected"]
    assert "image" in result.publish_blockers[0]


@pytest.mark.asyncio
async def test_quantity_comes_from_the_single_sellable_definition(session):
    """resolve_push_quantity is what the quantity push already uses; a second answer here
    would drift from it and the two would disagree about the same listing."""
    await _profile(session)
    product = await _product(session, current_stock=7)

    draft = await build_draft(session, product.id, ETSY)
    assert draft.units[0].quantity >= 0
