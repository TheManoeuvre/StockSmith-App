"""Tests for variant generation's BOM rule validation.

services/variants.py had no test coverage at all before this file, and the failure it's
mainly about was a raw IntegrityError on uq_product_variant_materials_variant_material
reaching the user as a bare "Internal server error" — nothing in the message said which
attribute rule caused it or how to fix it.

One of the cases here (a substitution onto an un-ruled base line) raises no database error
at all and instead silently overstates max_buildable, so it can only be caught by
validation like this.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.models.material import Material, LegacyMaterialCategory, MaterialUnit
from app.models.material_type import MaterialType
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.product import AttributeMaterialRule, AttributeQuantityRule, VariantAttributeSpec
from app.services.variants import generate_variants
from sqlalchemy import select

# Ids are fixed so rules can reference materials without threading objects around.
LILAC, IVORY, OAK, MATTE_LILAC, GLUE = 1, 2, 3, 4, 5


@pytest_asyncio.fixture
async def product(session):
    """A product whose build BOM is Lilac Purple + Ivory White + Glue.

    Ivory White being on the BOM in its own right is the point of several tests: it's what
    a Colourway rule substituting onto Ivory White would collide with.
    """
    filament_type = MaterialType(id=1, name="PLA")
    session.add(filament_type)
    session.add_all([
        Material(id=LILAC, name="Lilac Purple", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=IVORY, name="Ivory White", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=OAK, name="Oak", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=MATTE_LILAC, name="Matte Lilac", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=GLUE, name="Glue", category=LegacyMaterialCategory.other,
                 unit=MaterialUnit.each, material_type_id=None),
    ])
    p = Product(id=1, name="Widget", sku="SKU-1")
    session.add(p)
    await session.flush()
    session.add_all([
        ProductMaterial(product_id=1, material_id=LILAC, qty_required=Decimal("10")),
        ProductMaterial(product_id=1, material_id=IVORY, qty_required=Decimal("5")),
        ProductMaterial(product_id=1, material_id=GLUE, qty_required=Decimal("1")),
    ])
    await session.commit()
    return p


def _colourway(**value_to_material) -> VariantAttributeSpec:
    return VariantAttributeSpec(
        name="Colourway",
        values=list(value_to_material),
        material_rules=[AttributeMaterialRule(base_material_id=LILAC, value_to_material_id=value_to_material)],
    )


async def _variant_count(session) -> int:
    return len((await session.execute(select(ProductVariant))).scalars().all())


# --- Rule 1: two rows landing on the same material ------------------------------------


async def test_substituting_onto_a_base_line_with_its_own_rule_is_rejected(session, product):
    """The backlog's original case. Colourway substitutes Lilac -> Ivory while a Size
    quantity rule already targets the real Ivory line, so the variant would need two Ivory
    rows and violate the unique constraint."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(
            session,
            1,
            [
                _colourway(Ivory=IVORY),
                VariantAttributeSpec(
                    name="Size",
                    values=["Large"],
                    quantity_rules=[
                        AttributeQuantityRule(base_material_id=IVORY, value_to_qty={"Large": Decimal("8")})
                    ],
                ),
            ],
        )

    assert exc.value.status_code == 400
    detail = exc.value.detail
    # Names the specific rule and material, not just "constraint violated".
    assert "Colourway 'Ivory'" in detail
    assert "Ivory White" in detail
    assert "two Ivory White lines" in detail


async def test_two_substitutions_onto_the_same_material_are_rejected(session, product):
    with pytest.raises(HTTPException) as exc:
        await generate_variants(
            session,
            1,
            [
                VariantAttributeSpec(
                    name="Colourway",
                    values=["Ivory"],
                    material_rules=[
                        AttributeMaterialRule(base_material_id=LILAC, value_to_material_id={"Ivory": OAK}),
                        AttributeMaterialRule(base_material_id=IVORY, value_to_material_id={"Ivory": OAK}),
                    ],
                ),
            ],
        )

    assert exc.value.status_code == 400
    assert "Oak" in exc.value.detail
    assert "one BOM line per material" in exc.value.detail


async def test_conflict_across_two_different_attributes_is_rejected(session, product):
    """The collision only exists for the combination — neither rule is wrong alone, which
    is why this can't be caught by looking at rules in isolation."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(
            session,
            1,
            [
                _colourway(Ivory=IVORY, Oak=OAK),
                VariantAttributeSpec(
                    name="Size",
                    values=["Large"],
                    quantity_rules=[
                        AttributeQuantityRule(base_material_id=IVORY, value_to_qty={"Large": Decimal("8")})
                    ],
                ),
            ],
        )

    assert exc.value.status_code == 400
    assert "Ivory White" in exc.value.detail


# --- Rule 2: the silent one -----------------------------------------------------------


async def test_substituting_onto_an_unruled_base_line_is_rejected(session, product):
    """Raises no database error — no override row exists for the untouched Ivory line to
    collide with. But the resolved BOM would then emit Ivory twice, and buildability takes
    min() of per-line bottlenecks rather than summing consumption, so the variant would
    consume Ivory twice while being costed and constrained as if it used it once."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, [_colourway(Ivory=IVORY)])

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert "Ivory White" in detail
    assert "understate" in detail


async def test_substituting_onto_a_material_not_on_the_bom_is_fine(session, product):
    """The same substitution is legitimate when the target isn't already a BOM line."""
    created = await generate_variants(session, 1, [_colourway(Oak=OAK)])

    assert [v.variant_name for v in created] == ["Oak"]
    rows = (await session.execute(select(ProductVariantMaterial))).scalars().all()
    assert [(r.material_id, r.replaces_material_id) for r in rows] == [(OAK, LILAC)]


# --- Ambiguity: two attributes driving one line ---------------------------------------


async def test_two_attributes_driving_one_line_is_rejected(session, product):
    """Today the last rule silently wins, so the outcome depends on the order the client
    happened to serialise the attributes array and a rule the user explicitly ticked is
    discarded without a word."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(
            session,
            1,
            [
                _colourway(Ivory=OAK),
                VariantAttributeSpec(
                    name="Finish",
                    values=["Matte"],
                    material_rules=[
                        AttributeMaterialRule(base_material_id=LILAC, value_to_material_id={"Matte": MATTE_LILAC})
                    ],
                ),
            ],
        )

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert "Colourway" in detail and "Finish" in detail
    assert "Lilac Purple" in detail


async def test_redundant_but_consistent_rules_are_allowed(session, product):
    """Both rules always produce the same material, so there's nothing to disambiguate —
    rejecting this would be a false positive on a harmless configuration."""
    created = await generate_variants(
        session,
        1,
        [
            _colourway(Ivory=OAK),
            VariantAttributeSpec(
                name="Finish",
                values=["Matte"],
                material_rules=[
                    AttributeMaterialRule(base_material_id=LILAC, value_to_material_id={"Matte": OAK})
                ],
            ),
        ],
    )

    assert len(created) == 1


# --- Nothing is written on a rejected request -----------------------------------------


async def test_a_rejected_request_writes_nothing(session, product):
    """Validation runs before any mutation. Previously the attribute names were persisted
    first, so a request that then failed left them behind."""
    with pytest.raises(HTTPException):
        await generate_variants(session, 1, [_colourway(Ivory=IVORY)])

    await session.rollback()
    refreshed = await session.get(Product, 1)
    assert refreshed.variant_attribute1_name is None
    assert await _variant_count(session) == 0


async def test_fractional_quantity_on_an_each_unit_material_is_rejected(session, product):
    """The per-variant editor has always validated this; generation never did, so a
    fractional each-unit quantity could be written here and nowhere else."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(
            session,
            1,
            [
                VariantAttributeSpec(
                    name="Size",
                    values=["Large"],
                    quantity_rules=[
                        AttributeQuantityRule(base_material_id=GLUE, value_to_qty={"Large": Decimal("1.5")})
                    ],
                )
            ],
        )

    assert exc.value.status_code == 400


# --- Existing variants are not revalidated --------------------------------------------


async def test_a_latent_conflict_in_an_existing_variant_does_not_block_new_ones(session, product):
    """Only combos this call creates are validated. An existing variant's overrides are
    never touched by generation, so a pre-existing problem must not stop the user adding a
    new attribute value."""
    session.add(
        ProductVariant(id=99, product_id=1, variant_name="Legacy", attribute1_value="Legacy")
    )
    await session.flush()
    # A row that today's validation would reject: substitution onto the live Ivory line.
    session.add(
        ProductVariantMaterial(
            variant_id=99, material_id=IVORY, replaces_material_id=LILAC, qty_required=Decimal("5")
        )
    )
    await session.commit()

    created = await generate_variants(
        session, 1, [VariantAttributeSpec(name="Colourway", values=["Legacy", "Oak"],
                                          material_rules=[AttributeMaterialRule(
                                              base_material_id=LILAC, value_to_material_id={"Oak": OAK})])]
    )

    assert [v.variant_name for v in created] == ["Oak"]


# --- The happy path still works -------------------------------------------------------


async def test_generation_writes_merged_overrides(session, product):
    """A material rule and a quantity rule on the SAME base line must merge into one row,
    not two conflicting ones."""
    created = await generate_variants(
        session,
        1,
        [
            _colourway(Oak=OAK),
            VariantAttributeSpec(
                name="Size",
                values=["Large"],
                quantity_rules=[
                    AttributeQuantityRule(base_material_id=LILAC, value_to_qty={"Large": Decimal("14")})
                ],
            ),
        ],
    )

    assert len(created) == 1
    rows = (await session.execute(select(ProductVariantMaterial))).scalars().all()
    assert len(rows) == 1
    assert rows[0].material_id == OAK
    assert rows[0].replaces_material_id == LILAC
    assert rows[0].qty_required == Decimal("14")
