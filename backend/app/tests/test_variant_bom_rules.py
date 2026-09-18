"""Tests for variant generation's BOM rule handling.

Two base BOM lines resolving onto the same material — a two-tone product whose Primary and
Accent colour attributes both pick Apple Green for one combination — used to be refused
outright: the override table was unique on (variant_id, material_id), so the second row
was a raw IntegrityError, and the variant where the second "line" was the product's own
untouched base line raised nothing but silently overstated max_buildable.

Now such a variant is legitimate (each line keeps its own quantity; buildability sums per
material) and generation instead ASKS: a 409 lists every affected combination, and the
client re-submits choosing to skip them or keep them.
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
from app.services.buildability import compute_variant_buildability
from app.services.variants import SHARED_MATERIAL_VARIANTS, generate_variants
from sqlalchemy import select

# Ids are fixed so rules can reference materials without threading objects around.
LILAC, IVORY, OAK, MATTE_LILAC, GLUE = 1, 2, 3, 4, 5


@pytest_asyncio.fixture
async def product(session):
    """A product whose build BOM is Lilac Purple + Ivory White + Glue.

    Ivory White being on the BOM in its own right is the point of several tests: it's what
    a Colourway rule substituting onto Ivory White would share the material with.
    """
    filament_type = MaterialType(id=1, name="PLA")
    session.add(filament_type)
    session.add_all([
        Material(id=LILAC, name="Lilac Purple", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1, current_qty=Decimal("100")),
        Material(id=IVORY, name="Ivory White", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1, current_qty=Decimal("100")),
        Material(id=OAK, name="Oak", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1, current_qty=Decimal("100")),
        Material(id=MATTE_LILAC, name="Matte Lilac", category=LegacyMaterialCategory.filament,
                 unit=MaterialUnit.g, material_type_id=1),
        Material(id=GLUE, name="Glue", category=LegacyMaterialCategory.other,
                 unit=MaterialUnit.each, material_type_id=None, current_qty=Decimal("100")),
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


def _shared(exc) -> dict:
    detail = exc.value.detail
    assert exc.value.status_code == 409
    assert detail["code"] == SHARED_MATERIAL_VARIANTS
    return detail


def _size_rule_on_ivory(qty: str = "8") -> VariantAttributeSpec:
    return VariantAttributeSpec(
        name="Size",
        values=["Large"],
        quantity_rules=[AttributeQuantityRule(base_material_id=IVORY, value_to_qty={"Large": Decimal(qty)})],
    )


# --- A shared material is a question, not an error ------------------------------------


async def test_substituting_onto_a_base_line_with_its_own_rule_asks(session, product):
    """The backlog's original case. Colourway substitutes Lilac -> Ivory while a Size
    quantity rule already targets the real Ivory line, so the variant would draw on Ivory
    from two lines. Nothing is written; the 409 names the rule and the material."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, [_colourway(Ivory=IVORY), _size_rule_on_ivory()])

    detail = _shared(exc)
    assert detail["new_variant_count"] == 1
    [shared] = detail["variants"]
    assert shared["variant_name"] == "Ivory / Large"
    assert "Colourway 'Ivory' substituting Lilac Purple" in shared["message"]
    assert "own Ivory White line (Size 'Large')" in shared["message"]
    assert "2 BOM lines" in shared["message"]
    assert await _variant_count(session) == 0


async def test_two_substitutions_onto_the_same_material_asks(session, product):
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

    [shared] = _shared(exc)["variants"]
    assert "Oak would be used by 2 BOM lines" in shared["message"]


async def test_only_the_overlapping_combinations_are_listed(session, product):
    """The overlap only exists for the combination — neither rule is wrong alone, which
    is why this can't be caught by looking at rules in isolation. The 409 lists just the
    combinations affected, out of everything the call would create."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, [_colourway(Ivory=IVORY, Oak=OAK), _size_rule_on_ivory()])

    detail = _shared(exc)
    assert detail["new_variant_count"] == 2
    assert [v["variant_name"] for v in detail["variants"]] == ["Ivory / Large"]
    assert "1 of 2 new variants" in detail["message"]


async def test_substituting_onto_an_unruled_base_line_asks(session, product):
    """Raises no database error — no override row exists for the untouched Ivory line. But
    the resolved BOM emits Ivory twice, so it's the same question as any other overlap."""
    with pytest.raises(HTTPException) as exc:
        await generate_variants(session, 1, [_colourway(Ivory=IVORY)])

    [shared] = _shared(exc)["variants"]
    assert "Colourway 'Ivory' substituting Lilac Purple, and the product's own Ivory White line" in shared["message"]


async def test_substituting_onto_a_material_not_on_the_bom_is_fine(session, product):
    """The same substitution is legitimate when the target isn't already a BOM line."""
    created = await generate_variants(session, 1, [_colourway(Oak=OAK)])

    assert [v.variant_name for v in created] == ["Oak"]
    rows = (await session.execute(select(ProductVariantMaterial))).scalars().all()
    assert [(r.material_id, r.replaces_material_id) for r in rows] == [(OAK, LILAC)]


# --- Skip: create the rest without them ------------------------------------------------


async def test_skip_creates_everything_but_the_overlapping_combinations(session, product):
    created = await generate_variants(
        session, 1, [_colourway(Ivory=IVORY, Oak=OAK), _size_rule_on_ivory()], on_shared_material="skip"
    )

    assert [v.variant_name for v in created] == ["Oak / Large"]


async def test_skip_with_nothing_to_skip_is_a_plain_generate(session, product):
    created = await generate_variants(session, 1, [_colourway(Oak=OAK)], on_shared_material="skip")

    assert [v.variant_name for v in created] == ["Oak"]


# --- Keep: two lines on one material, each with its own quantity ------------------------


def _lines(bom, material_id: int) -> list[Decimal]:
    return sorted(line.qty_required for line in bom if line.material_id == material_id)


async def test_keep_writes_both_lines_and_sums_them_for_buildability(session, product):
    """Ivory 8 (the product's own line, sized) + Ivory 10 (substituted in for Lilac) is 18
    per unit. 100 on hand allows 5 units — not the 10 or 12 either line alone would."""
    created = await generate_variants(
        session, 1, [_colourway(Ivory=IVORY), _size_rule_on_ivory()], on_shared_material="keep"
    )

    [variant] = created
    rows = (await session.execute(select(ProductVariantMaterial))).scalars().all()
    assert sorted((r.material_id, r.replaces_material_id or 0, Decimal(r.qty_required)) for r in rows) == [
        (IVORY, 0, Decimal("8")),
        (IVORY, LILAC, Decimal("10")),
    ]

    figures, _cost, bom = await compute_variant_buildability(session, 1, variant.id)
    assert _lines(bom, IVORY) == [Decimal("8"), Decimal("10")]
    assert _lines(bom, LILAC) == []
    assert figures.max_buildable == 5
    assert {line.line_max_buildable for line in bom if line.material_id == IVORY} == {5}


async def test_keep_two_substitutions_onto_one_material(session, product):
    """The two-tone case: both colour attributes pick the same colour for one combination.
    Lilac -> Oak (10) and Ivory -> Oak (5) — 15 Oak per unit, and both base lines gone."""
    [variant] = await generate_variants(
        session,
        1,
        [
            VariantAttributeSpec(
                name="Primary",
                values=["Oak"],
                material_rules=[AttributeMaterialRule(base_material_id=LILAC, value_to_material_id={"Oak": OAK})],
            ),
            VariantAttributeSpec(
                name="Accent",
                values=["Oak"],
                material_rules=[AttributeMaterialRule(base_material_id=IVORY, value_to_material_id={"Oak": OAK})],
            ),
        ],
        on_shared_material="keep",
    )

    figures, _cost, bom = await compute_variant_buildability(session, 1, variant.id)
    assert _lines(bom, OAK) == [Decimal("5"), Decimal("10")]
    assert _lines(bom, LILAC) == [] and _lines(bom, IVORY) == []
    assert figures.max_buildable == 6  # 100 // 15


async def test_keep_with_an_untouched_base_line_inherits_it(session, product):
    """Substituting Lilac -> Ivory with nothing else touching the Ivory line: the product's
    own Ivory 5 stays and the substituted Ivory 10 joins it."""
    [variant] = await generate_variants(session, 1, [_colourway(Ivory=IVORY)], on_shared_material="keep")

    figures, _cost, bom = await compute_variant_buildability(session, 1, variant.id)
    assert _lines(bom, IVORY) == [Decimal("5"), Decimal("10")]
    assert figures.max_buildable == 6  # 100 // 15


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
    first, so a request that then failed left them behind. A shared-material 409 is the
    same: it exists to ask, and nothing may change before the answer."""
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
    # A row generation would stop to ask about: substitution onto the live Ivory line.
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
