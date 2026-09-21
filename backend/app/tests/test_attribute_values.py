"""Tests for renaming an attribute value across a product's variants.

A value is a string on each variant, not a row, so the rename is a fan-out — and the two
things it must not do are as important as the one it must: never rewrite a sku_suffix,
never let a hand-edited variant name be regenerated.
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

import app.routers.products as products_router
from app.models.attribute_value_code import ProductAttributeValueCode
from app.models.listing import Listing, ListingPlatform
from app.models.product import Product
from app.models.variant import ProductVariant
from app.schemas.product import AttributeValueRenameRequest, VariantAttributeSpec
from app.services import attribute_values
from app.services.attribute_values import AttributeValueError, ValueConflictError
from app.services.variants import generate_variants


@pytest_asyncio.fixture
async def pencil_pot(session):
    """Brick Pencil Pot: Size x Colour, numeric SKU scheme, one hand-renamed variant and one
    disabled one."""
    p = Product(id=1, name="Brick Pencil Pot", sku="BPP")
    session.add(p)
    await session.flush()
    await generate_variants(
        session,
        1,
        [
            VariantAttributeSpec(name="Size", values=["4 Stud", "4 Stud Standard", "6 Stud"]),
            VariantAttributeSpec(name="Colour", values=["Red", "Blue"]),
        ],
    )
    variants = {
        (v.attribute1_value, v.attribute2_value): v
        for v in (await session.execute(select(ProductVariant))).scalars()
    }
    variants[("4 Stud Standard", "Red")].variant_name = "Standard Red (old stock)"
    variants[("4 Stud Standard", "Blue")].is_active = False
    await session.commit()
    return p


async def _by_combo(session):
    return {
        (v.attribute1_value, v.attribute2_value): v
        for v in (await session.execute(select(ProductVariant))).scalars()
    }


async def _codes(session, slot):
    rows = (
        await session.execute(
            select(ProductAttributeValueCode).where(ProductAttributeValueCode.attribute_slot == slot)
        )
    ).scalars()
    return {r.value: r.code for r in rows}


class TestRename:
    async def test_renames_every_variant_including_inactive(self, session, pencil_pot):
        result = await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        assert result.variants_updated == 2
        combos = await _by_combo(session)
        assert ("4 Stud Standard", "Red") not in combos
        assert ("4 Stud Std", "Red") in combos
        assert ("4 Stud Std", "Blue") in combos
        assert combos[("4 Stud Std", "Blue")].is_active is False

    async def test_regenerates_only_untouched_names(self, session, pencil_pot):
        await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        combos = await _by_combo(session)
        assert combos[("4 Stud Std", "Blue")].variant_name == "4 Stud Std / Blue"
        # Hand-edited: left exactly as the user wrote it.
        assert combos[("4 Stud Std", "Red")].variant_name == "Standard Red (old stock)"

    async def test_never_touches_sku_suffix(self, session, pencil_pot):
        before = {k: v.sku_suffix for k, v in (await _by_combo(session)).items()}

        await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        after = await _by_combo(session)
        assert after[("4 Stud Std", "Red")].sku_suffix == before[("4 Stud Standard", "Red")]
        assert after[("4 Stud Std", "Blue")].sku_suffix == before[("4 Stud Standard", "Blue")]

    async def test_code_row_follows_the_value(self, session, pencil_pot):
        before = await _codes(session, 1)

        await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        after = await _codes(session, 1)
        assert "4 Stud Standard" not in after
        assert after["4 Stud Std"] == before["4 Stud Standard"]

    async def test_next_generate_reuses_the_code(self, session, pencil_pot):
        code_before = (await _codes(session, 1))["4 Stud Standard"]
        await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        created = await generate_variants(
            session,
            1,
            [
                VariantAttributeSpec(name="Size", values=["4 Stud Std"]),
                VariantAttributeSpec(name="Colour", values=["Green"]),
            ],
        )

        assert len(created) == 1
        assert created[0].sku_suffix.startswith(f"{code_before:02d}-")

    async def test_strips_whitespace(self, session, pencil_pot):
        await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "  4 Stud Std ")
        assert ("4 Stud Std", "Red") in await _by_combo(session)

    async def test_same_value_is_a_noop(self, session, pencil_pot):
        result = await attribute_values.rename_value(session, 1, 1, "4 Stud", "4 Stud")
        assert result.variants_updated == 0

    async def test_reports_live_platforms(self, session, pencil_pot):
        combos = await _by_combo(session)
        session.add(
            Listing(
                product_id=1,
                variant_id=combos[("4 Stud", "Red")].id,
                platform=ListingPlatform.etsy,
                external_listing_id="123",
            )
        )
        session.add(Listing(product_id=1, variant_id=combos[("6 Stud", "Red")].id, platform=ListingPlatform.ebay))
        await session.commit()

        result = await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud Std")

        # Only the confirmed one — the eBay row has never matched a real listing.
        assert result.live_platforms == [ListingPlatform.etsy]


class TestRefusals:
    async def test_empty_value(self, session, pencil_pot):
        with pytest.raises(AttributeValueError, match="empty"):
            await attribute_values.rename_value(session, 1, 1, "4 Stud", "   ")

    async def test_unknown_value(self, session, pencil_pot):
        with pytest.raises(AttributeValueError, match="No variant"):
            await attribute_values.rename_value(session, 1, 1, "8 Stud", "Huge")

    async def test_bad_slot(self, session, pencil_pot):
        with pytest.raises(AttributeValueError, match="slot"):
            await attribute_values.rename_value(session, 1, 4, "4 Stud", "Huge")

    async def test_conflict_carries_the_existing_value(self, session, pencil_pot):
        with pytest.raises(ValueConflictError) as exc:
            await attribute_values.rename_value(session, 1, 1, "4 Stud Standard", "4 Stud")
        assert exc.value.existing_value == "4 Stud"
        # Nothing moved.
        assert ("4 Stud Standard", "Red") in await _by_combo(session)

    async def test_unknown_product(self, session):
        with pytest.raises(AttributeValueError, match="No product"):
            await attribute_values.rename_value(session, 99, 1, "a", "b")


class TestRouter:
    async def test_conflict_is_409_with_plain_detail(self, session, pencil_pot):
        with pytest.raises(HTTPException) as exc:
            await products_router.rename_attribute_value(
                product_id=1,
                payload=AttributeValueRenameRequest(slot=1, old_value="4 Stud Standard", new_value="4 Stud"),
                session=session,
            )
        assert exc.value.status_code == 409
        assert isinstance(exc.value.detail, str)

    async def test_success_shape(self, session, pencil_pot):
        result = await products_router.rename_attribute_value(
            product_id=1,
            payload=AttributeValueRenameRequest(slot=1, old_value="4 Stud Standard", new_value="4 Stud Std"),
            session=session,
        )
        assert result.variants_updated == 2
        assert result.live_platforms == []


class TestSlotRename:
    """PATCH /products/{id} with variant_attribute{n}_name — a label change, never a
    structural one."""

    async def _patch(self, session, **fields):
        from app.routers.products import update_product
        from app.schemas.product import ProductUpdate

        return await update_product(1, ProductUpdate(**fields), session)

    async def test_renames_a_slot(self, session, pencil_pot):
        updated = await self._patch(session, variant_attribute1_name="Stud size")
        assert updated.variant_attribute1_name == "Stud size"
        assert updated.variant_attribute2_name == "Colour"

    async def test_strips_whitespace(self, session, pencil_pot):
        updated = await self._patch(session, variant_attribute1_name="  Stud size ")
        assert updated.variant_attribute1_name == "Stud size"

    async def test_refuses_clearing_a_slot_with_values(self, session, pencil_pot):
        with pytest.raises(HTTPException) as exc:
            await self._patch(session, variant_attribute2_name="")
        assert exc.value.status_code == 400
        assert "cannot be cleared" in exc.value.detail

    async def test_allows_clearing_an_empty_slot(self, session, pencil_pot):
        product = await session.get(Product, 1)
        product.variant_attribute3_name = "Unused"
        await session.commit()
        updated = await self._patch(session, variant_attribute3_name=None)
        assert updated.variant_attribute3_name is None

    async def test_refuses_duplicate_names(self, session, pencil_pot):
        with pytest.raises(HTTPException) as exc:
            await self._patch(session, variant_attribute1_name="colour")
        assert exc.value.status_code == 400
        assert "different name" in exc.value.detail

    async def test_untouched_slots_are_left_alone(self, session, pencil_pot):
        updated = await self._patch(session, name="Brick Pot")
        assert updated.variant_attribute1_name == "Size"
