"""Tests for bulk-amending BOM overrides across an attribute value.

Overrides could previously only be set per attribute value at generation time, so a
mistake found afterwards had to be corrected variant by variant. This writes to many
variants at once, which is why preview is the default and why the merge is scoped
narrowly to the base lines actually named. Rows carry a provenance (source: "rule" or
"manual"); hand-edited rows are left alone unless the amend asks to include them.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

import app.routers.products as products_router
from app.models.kitting import ProductKittingMaterial, ProductVariantKittingMaterial
from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.material_type import MaterialType
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.routers import variants as variants_router
from app.schemas.product import BulkBomAmendLine, BulkBomAmendRequest
from app.schemas.variant import VariantBomLine

FILAMENT, IVORY, OAK, GLUE, BOX = 1, 2, 3, 4, 5


@pytest_asyncio.fixture
async def product(session):
    """Product with a Size attribute (Large/Small) and a Colour attribute, BOM =
    Filament 10g + Glue 1."""
    session.add(MaterialType(id=1, name="PLA"))
    session.add_all([
        Material(id=FILAMENT, name="Filament", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=IVORY, name="Ivory White", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=OAK, name="Oak", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=GLUE, name="Glue", category=LegacyMaterialCategory.other, unit=MaterialUnit.each),
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


async def test_landing_on_a_material_another_line_already_uses_is_allowed(session, product, pushes):
    """Variant 10 already substitutes Glue -> Oak from a different attribute; amending
    Filament -> Oak for all Large variants gives it two Oak lines. That used to be a
    unique-constraint collision and a 400. Now a variant may draw on one material from
    several lines (each with its own quantity), so the amend goes through and the preview
    simply shows the resulting material per line."""
    session.add(
        ProductVariantMaterial(
            variant_id=10, material_id=OAK, replaces_material_id=GLUE, qty_required=Decimal("1")
        )
    )
    await session.commit()

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, material_id=OAK)],
        apply=True,
    )

    assert len(result.units) == 2
    rows = await _rows(session, 10)
    assert sorted((r.material_id, r.replaces_material_id) for r in rows) == [(OAK, FILAMENT), (OAK, GLUE)]


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


# --- Kitting BOM (is_kitting=True) ------------------------------------------------------


async def _kitting_rows(session, variant_id):
    return (
        await session.execute(
            select(ProductVariantKittingMaterial).where(
                ProductVariantKittingMaterial.variant_id == variant_id
            )
        )
    ).scalars().all()


@pytest_asyncio.fixture
async def kitting_bom(session, product):
    """Adds a packaging BOM (Box) to the same product/variants used by the build-BOM
    fixture, distinct material ids so a bug that reused the build-BOM table would be
    caught immediately."""
    session.add(
        Material(id=BOX, name="Box", category=LegacyMaterialCategory.other, unit=MaterialUnit.each)
    )
    await session.flush()
    session.add(ProductKittingMaterial(product_id=1, material_id=BOX, qty_required=Decimal("1")))
    await session.commit()


async def test_kitting_amend_is_isolated_from_build_bom(session, product, kitting_bom, pushes):
    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=BOX, qty_required=Decimal("2"))],
        apply=True,
        is_kitting=True,
    )

    assert result.applied is True
    for variant_id in (10, 11):
        rows = await _kitting_rows(session, variant_id)
        assert [(r.material_id, r.qty_required) for r in rows] == [(BOX, Decimal("2"))]
    # The build BOM overrides table is untouched.
    assert await _rows(session, 10) == []


async def test_kitting_amend_rejects_a_build_bom_material_id(session, product, kitting_bom):
    with pytest.raises(HTTPException) as exc_info:
        await _amend(
            session,
            attribute_name="Size",
            attribute_value="Large",
            lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
            is_kitting=True,
        )
    assert exc_info.value.status_code == 400
    assert "kitting BOM" in exc_info.value.detail


async def test_kitting_amend_preview_reports_existing_substitution(session, product, kitting_bom):
    session.add(
        ProductVariantKittingMaterial(
            variant_id=10, material_id=BOX, replaces_material_id=None, qty_required=Decimal("3")
        )
    )
    await session.commit()

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=BOX, qty_required=Decimal("5"))],
        is_kitting=True,
    )

    by_variant = {u.variant_id: u for u in result.units}
    change = by_variant[10].changes[0]
    assert change.before_qty == Decimal("3")
    assert change.after_qty == Decimal("5")


# --- Provenance: hand edits are left alone ---------------------------------------------


async def _hand_edited_row(session, variant_id=10, qty="12"):
    session.add(
        ProductVariantMaterial(variant_id=variant_id, material_id=FILAMENT, qty_required=Decimal(qty), source="manual")
    )
    await session.commit()


async def test_a_hand_edited_row_is_kept_by_default(session, product, pushes):
    await _hand_edited_row(session)

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    # Variant 10 keeps its hand-set 12; variant 11 (no row) is amended as usual.
    assert [(r.qty_required, r.source) for r in await _rows(session, 10)] == [(Decimal("12"), "manual")]
    assert [(r.qty_required, r.source) for r in await _rows(session, 11)] == [(Decimal("14"), "rule")]
    assert result.kept_manual_count == 1
    assert result.changed_variant_count == 1
    unit = next(u for u in result.units if u.variant_id == 10)
    assert unit.changes == []
    assert [(c.before_qty, c.after_qty) for c in unit.kept_manual] == [(Decimal("12"), Decimal("14"))]
    assert ("product", 1, 10) not in pushes  # nothing changed, so nothing to push


async def test_include_manual_overwrites_a_hand_edited_row(session, product, pushes):
    await _hand_edited_row(session)

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        include_manual=True,
        apply=True,
    )

    assert [(r.qty_required, r.source) for r in await _rows(session, 10)] == [(Decimal("14"), "rule")]
    assert result.kept_manual_count == 0


async def test_rows_from_before_the_source_column_are_replaced_as_before(session, product, pushes):
    """The column defaults to "rule", so existing data behaves exactly as it did."""
    session.add(ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12")))
    await session.commit()

    await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
        apply=True,
    )

    assert [(r.qty_required, r.source) for r in await _rows(session, 10)] == [(Decimal("14"), "rule")]


async def test_a_hand_edited_row_already_correct_is_not_reported_as_kept(session, product):
    await _hand_edited_row(session, qty="14")

    result = await _amend(
        session,
        attribute_name="Size",
        attribute_value="Large",
        lines=[BulkBomAmendLine(base_material_id=FILAMENT, qty_required=Decimal("14"))],
    )

    assert result.kept_manual_count == 0


# --- Provenance: how the variant BOM editor stamps rows ---------------------------------


async def test_saving_in_the_editor_marks_new_and_changed_rows_manual(session, product, pushes):
    await variants_router.replace_bom_overrides(
        variant_id=10,
        payload=[VariantBomLine(material_id=FILAMENT, qty_required=Decimal("12"))],
        session=session,
    )

    assert [(r.qty_required, r.source) for r in await _rows(session, 10)] == [(Decimal("12"), "manual")]


async def test_saving_in_the_editor_does_not_claim_rows_it_did_not_change(session, product, pushes):
    """The editor re-sends the whole list on every save, so a rule-generated row that
    comes back untouched must stay "rule" — otherwise one edit anywhere would make every
    row on the variant look hand-made."""
    session.add_all([
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12"), source="rule"),
        ProductVariantMaterial(variant_id=10, material_id=GLUE, qty_required=Decimal("2"), source="rule"),
    ])
    await session.commit()

    await variants_router.replace_bom_overrides(
        variant_id=10,
        payload=[
            VariantBomLine(material_id=FILAMENT, qty_required=Decimal("12")),  # unchanged
            VariantBomLine(material_id=GLUE, qty_required=Decimal("3")),  # edited
        ],
        session=session,
    )

    sources = {r.material_id: r.source for r in await _rows(session, 10)}
    assert sources == {FILAMENT: "rule", GLUE: "manual"}


async def test_a_variant_merge_keeps_each_rows_source(session, product):
    from app.services.variant_merge import _copy_overrides

    session.add(
        ProductVariantMaterial(variant_id=10, material_id=FILAMENT, qty_required=Decimal("12"), source="manual")
    )
    await session.commit()

    await _copy_overrides(session, ProductVariantMaterial, loser_id=10, survivor_id=12)
    await session.commit()

    assert [(r.qty_required, r.source) for r in await _rows(session, 12)] == [(Decimal("12"), "manual")]
