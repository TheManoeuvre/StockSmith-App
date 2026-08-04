"""Tests for bulk-amending BOM overrides across an attribute value.

Overrides could previously only be set per attribute value at generation time, so a
mistake found afterwards had to be corrected variant by variant. This writes to many
variants at once and cannot tell a rule-generated row from a hand-edited one
(ProductVariantMaterial has no provenance column), which is why preview is the default
and why the merge is scoped narrowly to the base lines actually named.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

import app.routers.products as products_router
from app.models.material import Material, MaterialCategory, MaterialUnit
from app.models.material_type import MaterialType
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.product import BulkBomAmendLine, BulkBomAmendRequest

FILAMENT, IVORY, OAK, GLUE = 1, 2, 3, 4


@pytest_asyncio.fixture
async def product(session):
    """Product with a Size attribute (Large/Small) and a Colour attribute, BOM =
    Filament 10g + Glue 1."""
    session.add(MaterialType(id=1, name="PLA"))
    session.add_all([
        Material(id=FILAMENT, name="Filament", category=MaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=IVORY, name="Ivory White", category=MaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=OAK, name="Oak", category=MaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=GLUE, name="Glue", category=MaterialCategory.other, unit=MaterialUnit.each),
    ])
    p = Product(
        id=1, name="Widget", sku="SKU-1",
        variant_attribute1_name="Size", variant_attribute2_name="Colour",
    )
    session.add(p)
    await session.flush()
    session.add_all([
        ProductMaterial(product_id=1, material_id=FILAMENT, qty_required=Decimal("10")),
        ProductMaterial(product_id=1, material_id=GLUE, qty_required=Decimal("1")),
    ])
    session.add_all([
        ProductVariant(id=10, product_id=1, variant_name="Large / Red",
                       attribute1_value="Large", attribute2_value="Red"),
        ProductVariant(id=11, product_id=1, variant_name="Large / Blue",
                       attribute1_value="Large", attribute2_value="Blue"),
        ProductVariant(id=12, product_id=1, variant_name="Small / Red",
                       attribute1_value="Small", attribute2_value="Red"),
    ])
    await session.commit()
    return p


async def _amend(session, **kwargs):
    payload = BulkBomAmendRequest(**kwargs)
    return await products_router.amend_variant_bom_overrides(product_id=1, payload=payload, session=session)


async def _rows(session, variant_id):
    return (
        await session.execute(
            select(ProductVariantMaterial).where(ProductVariantMaterial.variant_id == variant_id)
        )
    ).scalars().all()


# --- Preview is the default -----------------------------------------------------------


async def test_preview_writes_nothing(session, product, pushes):
    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )

    assert result.applied is False
    assert result.matched_variant_count == 2
    assert result.changed_variant_count == 2
    assert await _rows(session, 10) == []
    assert pushes == []  # nothing pushed for a preview either


async def test_preview_reports_before_and_after(session, product):
    """The preview is what makes overwriting hand edits consensual — it has to show the
    value it would replace, since nothing else can distinguish one."""
    session.add(
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12"))
    )
    await session.commit()

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )

    by_variant = {u.variant_id: u for u in result.units}
    change = by_variant[10].changes[0]
    assert change.before_qty == Decimal("12")  # the hand edit, shown before it's replaced
    assert change.after_qty == Decimal("14")
    # The variant that had no override reports inheriting the base BOM.
    assert by_variant[11].changes[0].before_qty is None


# --- Applying --------------------------------------------------------------------------


async def test_apply_writes_to_every_matching_variant(session, product, pushes):
    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    assert result.applied is True
    for variant_id in (10, 11):
        rows = await _rows(session, variant_id)
        assert [(r.material_id, r.qty_required) for r in rows] == [(FILAMENT, Decimal("14"))]
    # The non-matching variant is untouched.
    assert await _rows(session, 12) == []
    # A BOM change moves the quantity pushed to the marketplace.
    assert ("product", 1, 10) in pushes and ("product", 1, 11) in pushes


async def test_apply_replaces_rather_than_duplicating(session, product, pushes):
    session.add(
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12"))
    )
    await session.commit()

    await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    rows = await _rows(session, 10)
    assert len(rows) == 1  # replaced, not added alongside
    assert rows[0].qty_required == Decimal("14")


async def test_amending_back_to_the_base_bom_removes_the_row(session, product, pushes):
    """An override equal to the base BOM is not a row — same rule the generator uses."""
    session.add(
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12"))
    )
    await session.commit()

    await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("10"))],
        apply=True,
    )

    assert await _rows(session, 10) == []


async def test_unnamed_base_lines_and_additive_rows_are_untouched(session, product, pushes):
    """The merge is scoped to the base lines actually named. A hand-added extra line
    belongs to no base line at all and must survive."""
    session.add_all([
        ProductVariantMaterial(variant_id=10, material_id=GLUE, qty_required=Decimal("3")),
        ProductVariantMaterial(variant_id=10, material_id=OAK, qty_required=Decimal("2")),
    ])
    await session.commit()

    await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    rows = {r.material_id: r.qty_required for r in await _rows(session, 10)}
    assert rows[GLUE] == Decimal("3")  # different base line, not named
    assert rows[OAK] == Decimal("2")  # additive extra line
    assert rows[FILAMENT] == Decimal("14")


async def test_substitution_is_written_with_replaces_material_id(session, product, pushes):
    await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, material_id=OAK)],
        apply=True,
    )

    rows = await _rows(session, 10)
    assert [(r.material_id, r.replaces_material_id, r.qty_required) for r in rows] == [
        (OAK, FILAMENT, Decimal("10"))  # quantity inherited from the base line
    ]


# --- Validation ------------------------------------------------------------------------


async def test_collision_with_an_existing_override_is_rejected(session, product):
    """The amend can collide with a row this variant already has from a different
    attribute — which neither the existing rows nor the new ones would reveal alone."""
    session.add(
        ProductVariantMaterial(
            variant_id=10, material_id=OAK, replaces_material_id=GLUE, qty_required=Decimal("1")
        )
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await _amend(
            session,
            attribute_name="Size",
            attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=FILAMENT, material_id=OAK)],
        )

    assert exc.value.status_code == 400
    assert "Oak" in exc.value.detail


async def test_conflicts_are_rejected_in_preview_too(session, product):
    """Preview must be a real pre-flight, not just a happy-path renderer."""
    session.add(
        ProductVariantMaterial(
            variant_id=10, material_id=OAK, replaces_material_id=GLUE, qty_required=Decimal("1")
        )
    )
    await session.commit()

    with pytest.raises(HTTPException):
        await _amend(
            session,
            attribute_name="Size",
            attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=FILAMENT, material_id=OAK)],
            apply=False,
        )


async def test_cross_material_type_substitution_is_rejected(session, product):
    with pytest.raises(HTTPException) as exc:
        await _amend(
            session,
            attribute_name="Size",
            attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=FILAMENT, material_id=GLUE)],
        )

    assert "material type" in exc.value.detail


async def test_fractional_quantity_on_an_each_unit_material_is_rejected(session, product):
    with pytest.raises(HTTPException):
        await _amend(
            session,
            attribute_name="Size",
            attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=GLUE, qty_required=Decimal("1.5"))],
        )


async def test_unknown_attribute_name_lists_the_real_ones(session, product):
    with pytest.raises(HTTPException) as exc:
        await _amend(
            session, attribute_name="Flavour", attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        )

    assert exc.value.status_code == 400
    assert "Size" in exc.value.detail and "Colour" in exc.value.detail


async def test_attribute_name_matching_ignores_case_and_padding(session, product):
    """The name comes from a form field the user typed, not a picker."""
    result = await _amend(
        session, attribute_name="  size  ", attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )
    assert result.matched_variant_count == 2


async def test_base_material_not_on_the_bom_is_rejected(session, product):
    with pytest.raises(HTTPException) as exc:
        await _amend(
            session, attribute_name="Size", attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=OAK, qty_required=Decimal("1"))],
        )

    assert "not on this product's build BOM" in exc.value.detail


# --- Scope ------------------------------------------------------------------------------


async def test_no_matching_variants_is_an_empty_result_not_an_error(session, product):
    """A preview showing zero is clearer feedback than a 404 on a value that simply has no
    variants yet."""
    result = await _amend(
        session, attribute_name="Size", attribute_value="Enormous",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )

    assert result.matched_variant_count == 0
    assert result.units == []


async def test_inactive_variants_are_skipped_by_default(session, product):
    variant = await session.get(ProductVariant, 11)
    variant.is_active = False
    await session.commit()

    result = await _amend(
        session, attribute_name="Size", attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )

    assert result.matched_variant_count == 2
    assert result.skipped_inactive_count == 1
    assert [u.variant_id for u in result.units] == [10]


async def test_a_variant_already_correct_reports_no_change(session, product, pushes):
    session.add(
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("14"))
    )
    await session.commit()

    result = await _amend(
        session, attribute_name="Size", attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    by_variant = {u.variant_id: u for u in result.units}
    assert by_variant[10].changes == []
    assert result.changed_variant_count == 1  # only variant 11 actually changed
    # An unchanged variant shouldn't cost a marketplace push.
    assert ("product", 1, 10) not in pushes
