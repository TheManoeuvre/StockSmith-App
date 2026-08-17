import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import product as cartesian_product

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.material import Material
from app.models.product import Product, ProductMaterial
from app.models.sku_alias import SkuAlias
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.product import VariantAttributeSpec
from app.services.validation import validate_lines_against_units

_SLUG_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def slugify(value: str) -> str:
    """Uppercase, non-alphanumeric runs collapsed to a single hyphen, no leading/trailing
    hyphens — e.g. "X-Large" -> "X-LARGE", "Sea Foam Green" -> "SEA-FOAM-GREEN"."""
    return _SLUG_NON_ALNUM.sub("-", value).strip("-").upper()


def compute_full_sku(product_sku: str | None, sku_suffix: str | None) -> str | None:
    if product_sku and sku_suffix:
        return f"{product_sku}-{sku_suffix}"
    return product_sku


async def find_by_sku(
    session: AsyncSession, sku: str, platform: ListingPlatform | None = None
) -> tuple[int | None, int | None] | None:
    """Resolves a raw SKU string (e.g. from an incoming marketplace order line) to a
    (product_id, variant_id) pair. Checks a remembered SkuAlias first (when a platform is
    given — a human previously mapped this exact marketplace SKU to a product/variant
    that has its own, different SKU); then tries an exact Product.sku match (variant_id
    None); then falls back to matching a variant's full_sku (product.sku + "-" +
    sku_suffix). Returns None if nothing matches."""
    sku = sku.strip()
    if not sku:
        return None

    if platform is not None:
        alias_result = await session.execute(
            select(SkuAlias.product_id, SkuAlias.variant_id).where(
                SkuAlias.platform == platform, SkuAlias.external_sku == sku
            )
        )
        alias_row = alias_result.first()
        if alias_row is not None:
            return alias_row[0], alias_row[1]

    result = await session.execute(select(Product.id).where(Product.sku == sku))
    product_id = result.scalar_one_or_none()
    if product_id is not None:
        return product_id, None

    result = await session.execute(
        select(ProductVariant.id, ProductVariant.product_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.sku_suffix.is_not(None), Product.sku + "-" + ProductVariant.sku_suffix == sku)
    )
    row = result.first()
    if row is not None:
        variant_id, matched_product_id = row
        return matched_product_id, variant_id

    return None


async def _validate_attribute_rules(
    session: AsyncSession, product_id: int, attributes: list[VariantAttributeSpec]
) -> tuple[dict[int, ProductMaterial], dict[int, str]]:
    """Checks every rule's base_material_id is actually on the product's build BOM, and
    every material-rule candidate shares the base material's material_type_id — a
    stricter check than the manual per-variant editor's category-only validation
    (_validate_substitution_categories in routers/variants.py), since here the "same
    material, different colour" grouping is the entire point, and a same-category-but-
    different-type swap (e.g. PLA for PETG) would be a real hazard when it's silently
    applied across dozens of auto-generated variants rather than reviewed by hand.

    Returns the base ProductMaterial rows referenced by any rule, keyed by material_id,
    for the caller to reuse without re-querying."""
    base_material_ids = {
        rule.base_material_id for spec in attributes for rule in (*spec.material_rules, *spec.quantity_rules)
    }
    if not base_material_ids:
        return {}, {}

    result = await session.execute(
        select(ProductMaterial).where(
            ProductMaterial.product_id == product_id, ProductMaterial.material_id.in_(base_material_ids)
        )
    )
    base_lines_by_material_id = {pm.material_id: pm for pm in result.scalars()}
    missing = base_material_ids - set(base_lines_by_material_id)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material(s) {sorted(missing)} are not on this product's build BOM",
        )

    candidate_material_ids = {
        material_id
        for spec in attributes
        for rule in spec.material_rules
        for material_id in rule.value_to_material_id.values()
    }
    # Fetched unconditionally, not just when there are material rules: conflict messages
    # name materials, and a quantity-rule-only collision needs those names just as much.
    result = await session.execute(
        select(Material.id, Material.name, Material.material_type_id).where(
            Material.id.in_(candidate_material_ids | base_material_ids)
        )
    )
    rows = result.all()
    name_by_material_id = {row.id: row.name for row in rows}
    type_by_material_id = {row.id: row.material_type_id for row in rows}

    for spec in attributes:
        for rule in spec.material_rules:
            base_type = type_by_material_id.get(rule.base_material_id)
            for candidate_id in rule.value_to_material_id.values():
                candidate_type = type_by_material_id.get(candidate_id)
                if candidate_type is None or base_type != candidate_type:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Material {candidate_id} is not the same material type as base "
                            f"material {rule.base_material_id} — can't auto-substitute across types"
                        ),
                    )

    ambiguities = _detect_rule_ambiguities(attributes, name_by_material_id)
    if ambiguities:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=ambiguities[0])

    return base_lines_by_material_id, name_by_material_id


@dataclass
class ResolvedOverride:
    """One variant BOM override row, plus which attribute value produced it.

    The provenance fields exist so a conflict can be reported as "Colourway 'Ivory' on
    Lilac Purple conflicts with…" rather than as bare material ids — by the time a
    collision is detected the rules that caused it are otherwise long out of scope. Being
    a plain dataclass rather than a ProductVariantMaterial also makes the resolver
    testable without a session."""

    base_material_id: int
    material_id: int
    replaces_material_id: int | None
    qty_required: Decimal
    material_source: tuple[str, str] | None = None  # (attribute name, value) that set the material
    qty_source: tuple[str, str] | None = None  # (attribute name, value) that set the quantity

    def to_row(self, variant_id: int) -> ProductVariantMaterial:
        return ProductVariantMaterial(
            variant_id=variant_id,
            material_id=self.material_id,
            replaces_material_id=self.replaces_material_id,
            qty_required=self.qty_required,
        )


def _resolve_line_overrides(
    combo: tuple[str, ...],
    attributes: list[VariantAttributeSpec],
    base_lines_by_material_id: dict[int, ProductMaterial],
) -> list[ResolvedOverride]:
    """Merges every material/quantity rule that fires for this specific attribute-value
    combo into at most one effective (material_id, qty_required) per base BOM line —
    e.g. Colour substituting the material and Size overriding the qty on the SAME base
    line both land on one row, not two conflicting ones (the override table has a unique
    constraint on (variant_id, material_id)).

    Note that merging per BASE line does not guarantee one row per EFFECTIVE material:
    two base lines can resolve onto the same target. That's what _collision_messages is
    for — this function deliberately reports what the rules say rather than silently
    dropping one of them."""
    # base_material_id -> [effective_material_id, effective_qty | None, material_source, qty_source]
    effective: dict[int, list] = {}
    for i, spec in enumerate(attributes):
        value = combo[i]
        for rule in spec.material_rules:
            if value in rule.value_to_material_id:
                entry = effective.setdefault(rule.base_material_id, [rule.base_material_id, None, None, None])
                entry[0] = rule.value_to_material_id[value]
                entry[2] = (spec.name, value)
        for rule in spec.quantity_rules:
            if value in rule.value_to_qty:
                entry = effective.setdefault(rule.base_material_id, [rule.base_material_id, None, None, None])
                entry[1] = rule.value_to_qty[value]
                entry[3] = (spec.name, value)

    rows: list[ResolvedOverride] = []
    for base_id, (material_id, qty, material_source, qty_source) in effective.items():
        base_line = base_lines_by_material_id[base_id]
        base_qty = Decimal(base_line.qty_required)
        final_qty = qty if qty is not None else base_qty
        if material_id == base_id and final_qty == base_qty:
            continue  # no actual change from the base BOM — nothing to write
        rows.append(
            ResolvedOverride(
                base_material_id=base_id,
                material_id=material_id,
                replaces_material_id=base_id if material_id != base_id else None,
                qty_required=final_qty,
                material_source=material_source,
                qty_source=qty_source,
            )
        )
    return rows


async def _product_base_material_ids(session: AsyncSession, product_id: int) -> set[int]:
    """Every material on the product's build BOM — the whole BOM, not just the lines rules
    reference, since Rule 2 asks whether a substitution *target* is already on it."""
    result = await session.execute(
        select(ProductMaterial.material_id).where(ProductMaterial.product_id == product_id)
    )
    return {row[0] for row in result}


def _name(name_by_material_id: dict[int, str], material_id: int) -> str:
    return name_by_material_id.get(material_id, f"material {material_id}")


def _source_phrase(source: tuple[str, str] | None) -> str | None:
    return f"{source[0]} '{source[1]}'" if source else None


def _collision_messages(
    rows: list[ResolvedOverride],
    base_material_ids: set[int],
    name_by_material_id: dict[int, str],
    variant_name: str,
) -> list[str]:
    """Every way one variant's override rows can be invalid, checked before any write.

    Rule 1 — two rows landing on the same effective material. This is what the database's
    uq_product_variant_materials_variant_material rejects, today as a raw IntegrityError
    that reaches the user as a bare "Internal server error" with no indication of which
    rule caused it.

    Rule 2 — a substitution onto a material that is itself a base BOM line which nothing
    substitutes away. This violates no constraint, which is why it has gone unnoticed:
    no override row exists for the untouched base line, so there is nothing to collide
    with. But buildability._RESOLVED_VARIANT_BOM_SQL then emits that material twice (once
    from product_materials, once from the substitution row), and
    compute_variant_buildability takes min() of per-line bottlenecks against the same
    current_qty rather than summing consumption — so the variant consumes the material
    twice while being constrained as if it used it once, and max_buildable overstates.

    Shared with the bulk-amend path, which runs the same rules against a variant's
    resulting rows rather than freshly-resolved ones."""
    messages: list[str] = []

    by_material: dict[int, list[ResolvedOverride]] = {}
    for row in rows:
        by_material.setdefault(row.material_id, []).append(row)

    for material_id, colliding in by_material.items():
        if len(colliding) < 2:
            continue
        material = _name(name_by_material_id, material_id)
        # The most common shape, and the one worth phrasing specifically: something is
        # being substituted onto a base line that already has its own override row.
        own_line = next((r for r in colliding if r.replaces_material_id is None), None)
        substitutions = [r for r in colliding if r.replaces_material_id is not None]
        if own_line is not None and substitutions:
            sub = substitutions[0]
            source = _source_phrase(sub.material_source) or "a rule"
            messages.append(
                f"{source} on {_name(name_by_material_id, sub.base_material_id)} conflicts with the existing "
                f"{material} BOM line — variant '{variant_name}' would need two {material} lines. "
                f"Remove the rule on the {material} line, or substitute "
                f"{_name(name_by_material_id, sub.base_material_id)} with a material that isn't already on "
                "this product's BOM."
            )
            continue
        if len(substitutions) >= 2:
            first, second = substitutions[0], substitutions[1]
            first_source = _source_phrase(first.material_source) or "one rule"
            second_source = _source_phrase(second.material_source) or "another rule"
            messages.append(
                f"Variant '{variant_name}': {first_source} puts the "
                f"{_name(name_by_material_id, first.base_material_id)} line on {material} and {second_source} "
                f"puts the {_name(name_by_material_id, second.base_material_id)} line on {material} — a variant "
                "can only have one BOM line per material. Adjust one of the substitutions."
            )
            continue
        messages.append(
            f"Variant '{variant_name}' would end up with two {material} BOM lines. "
            "A variant can only have one line per material."
        )

    # Rule 2. Only substitutions can trigger it, and only onto a base line that no rule
    # moves out of the way.
    substituted_away = {row.replaces_material_id for row in rows if row.replaces_material_id is not None}
    for row in rows:
        if row.replaces_material_id is None:
            continue
        if row.material_id in by_material and len(by_material[row.material_id]) > 1:
            continue  # already reported as a Rule 1 collision
        if row.material_id in base_material_ids and row.material_id not in substituted_away:
            material = _name(name_by_material_id, row.material_id)
            source = _source_phrase(row.material_source) or "This substitution"
            messages.append(
                f"Variant '{variant_name}': {source} substituting "
                f"{_name(name_by_material_id, row.base_material_id)} with {material} would leave two {material} "
                f"lines on the variant — the substituted one plus this product's own {material} BOM line — "
                f"which would understate how much {material} the variant uses. Merge them into one line, or "
                "substitute to a material that isn't already on the BOM."
            )
    return messages


def _detect_rule_ambiguities(
    attributes: list[VariantAttributeSpec], name_by_material_id: dict[int, str]
) -> list[str]:
    """Two different attributes driving the same base BOM line's material (or quantity).

    Rule-shaped rather than combo-shaped, so it's detected analytically — which also gives
    a far better message than any single combination could ("both Colourway and Finish set
    the material for the Lilac Purple line") .

    Today the last rule in the list silently wins, which means the outcome depends on the
    order the client happened to serialise the attributes array and a rule the user
    explicitly configured is discarded without a word. Flagged only when the two rules can
    actually produce DIFFERENT outcomes: redundant-but-consistent configurations are
    harmless and rejecting them would be a false positive."""
    messages: list[str] = []

    def _check(kind: str, rules_of: str, value_map: str) -> None:
        # base_material_id -> list of (attribute name, value -> outcome)
        by_base: dict[int, list[tuple[str, dict]]] = {}
        for spec in attributes:
            for rule in getattr(spec, rules_of):
                by_base.setdefault(rule.base_material_id, []).append((spec.name, getattr(rule, value_map)))

        for base_id, entries in by_base.items():
            if len(entries) < 2:
                continue
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    (name_a, map_a), (name_b, map_b) = entries[i], entries[j]
                    # Different attributes, so ANY pair of values can co-occur in some
                    # variant — the only question is whether they'd disagree.
                    outcomes_a = set(map_a.values())
                    outcomes_b = set(map_b.values())
                    if outcomes_a == outcomes_b and len(outcomes_a) == 1:
                        continue  # both always produce the same thing — harmless
                    example_a = next(iter(map_a.items()), None)
                    example_b = next(iter(map_b.items()), None)
                    detail = ""
                    if example_a and example_b:
                        detail = (
                            f" ({name_a} '{example_a[0]}' → {_describe(kind, example_a[1], name_by_material_id)}, "
                            f"{name_b} '{example_b[0]}' → {_describe(kind, example_b[1], name_by_material_id)})"
                        )
                    messages.append(
                        f"Both '{name_a}' and '{name_b}' set the {kind} for the "
                        f"{_name(name_by_material_id, base_id)} BOM line{detail}. Only one attribute can drive a "
                        f"given BOM line's {kind} — remove one of the rules."
                    )

    _check("material", "material_rules", "value_to_material_id")
    _check("quantity", "quantity_rules", "value_to_qty")
    return messages


def _describe(kind: str, outcome, name_by_material_id: dict[int, str]) -> str:
    return _name(name_by_material_id, outcome) if kind == "material" else str(outcome)


async def generate_variants(
    session: AsyncSession, product_id: int, attributes: list[VariantAttributeSpec]
) -> list[ProductVariant]:
    """Persists up to 3 attribute names onto the product, computes the cartesian product
    of their values, and creates any combinations that don't already exist — existing
    variants (and their BOM overrides) are left untouched, so adding one new value to an
    existing attribute only creates the new combinations.

    Attributes can also carry material_rules/quantity_rules (see schemas.product) that
    auto-write the matching ProductVariantMaterial override rows for each newly-created
    variant — see _resolve_line_overrides for how multiple rules targeting the same base
    BOM line are merged. Rules are only ever applied to variants created in this call;
    an already-existing (skipped) combo's overrides are never touched here."""
    if not attributes or len(attributes) > 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide between 1 and 3 attributes")

    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    for spec in attributes:
        if not spec.values:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Attribute '{spec.name}' needs at least one value"
            )

    base_lines_by_material_id, name_by_material_id = await _validate_attribute_rules(
        session, product_id, attributes
    )

    # Read the existing combos BEFORE validating, because only combos this call will
    # actually CREATE may be validated. A pre-existing variant's overrides are never
    # touched here (see this function's docstring), so a latent conflict in one must not
    # block adding a new value to an attribute.
    result = await session.execute(select(ProductVariant).where(ProductVariant.product_id == product_id))
    existing_variants = list(result.scalars())
    existing_combos = {
        (v.attribute1_value, v.attribute2_value, v.attribute3_value) for v in existing_variants
    }
    combos_to_create = [
        combo
        for combo in cartesian_product(*(spec.values for spec in attributes))
        if tuple(list(combo) + [None] * (3 - len(combo))) not in existing_combos
    ]

    # Resolve and validate every new combo up front. Enumerating rather than reasoning
    # about the rules analytically because a collision depends on the JOINT choice across
    # attributes, and this is the same cartesian product the creation loop below walks —
    # doing it in memory with no database access is strictly cheaper than the flush-per-
    # combo that follows.
    base_material_ids = await _product_base_material_ids(session, product_id)
    resolved_by_combo: list[tuple[tuple[str, ...], list[ResolvedOverride]]] = []
    for combo in combos_to_create:
        rows = _resolve_line_overrides(combo, attributes, base_lines_by_material_id)
        messages = _collision_messages(rows, base_material_ids, name_by_material_id, " / ".join(combo))
        if messages:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=messages[0])
        resolved_by_combo.append((combo, rows))

    # The per-variant editor has always validated quantities against each material's unit
    # (routers/variants.replace_bom_overrides); generation never did, so a fractional
    # quantity on an each-unit material could be written here and nowhere else.
    await validate_lines_against_units(
        session,
        [(row.material_id, row.qty_required) for _, rows in resolved_by_combo for row in rows],
        "qty_required",
    )

    # Only now does anything mutate. Writing the attribute names before validation would
    # leave them persisted on a request that then 400s.
    names = [spec.name for spec in attributes] + [None] * (3 - len(attributes))
    product.variant_attribute1_name, product.variant_attribute2_name, product.variant_attribute3_name = names

    # Suffixes are built for the whole batch at once so code allocation happens in one
    # pass — see services/sku_generation for why a product never mixes the two schemes.
    from app.services import sku_generation

    suffixes = await sku_generation.build_suffixes(
        session, product_id, existing_variants, [combo for combo, _ in resolved_by_combo]
    )

    created: list[ProductVariant] = []
    for combo, rows in resolved_by_combo:
        padded = list(combo) + [None] * (3 - len(combo))
        variant = ProductVariant(
            product_id=product_id,
            variant_name=" / ".join(combo),
            sku_suffix=suffixes[combo],
            attribute1_value=padded[0],
            attribute2_value=padded[1],
            attribute3_value=padded[2],
        )
        session.add(variant)
        await session.flush()  # assigns variant.id, needed for the override rows below
        created.append(variant)

        for row in rows:
            session.add(row.to_row(variant.id))

    await session.commit()
    for variant in created:
        await session.refresh(variant)
    return created


_ATTRIBUTE_SLOTS = (
    ("variant_attribute1_name", "attribute1_value"),
    ("variant_attribute2_name", "attribute2_value"),
    ("variant_attribute3_name", "attribute3_value"),
)


def _resolve_attribute_slot(product: Product, attribute_name: str) -> str:
    """Maps an attribute name to the ProductVariant column holding its value.

    Attributes are denormalised onto three name columns on the product and three value
    columns on the variant — there is no attribute table — so this is the only way in.
    Matched case- and whitespace-insensitively because the name arrives from a form field
    the user typed, not from a picker."""
    wanted = attribute_name.strip().casefold()
    for name_attr, value_attr in _ATTRIBUTE_SLOTS:
        name = getattr(product, name_attr)
        if name is not None and name.strip().casefold() == wanted:
            return value_attr
    known = [getattr(product, n) for n, _ in _ATTRIBUTE_SLOTS if getattr(product, n)]
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            f"'{attribute_name}' is not an attribute of this product"
            + (f" — it has {', '.join(repr(k) for k in known)}" if known else " (it has no variant attributes)")
        ),
    )


async def amend_attribute_bom_overrides(
    session: AsyncSession,
    product_id: int,
    attribute_name: str,
    attribute_value: str,
    lines: list,
    *,
    apply: bool = False,
    include_inactive: bool = False,
) -> tuple[list, int, int]:
    """Rewrites one base BOM line per amend line, across every variant sharing an
    attribute value — "set the Ivory White quantity to 3 for all Large variants".

    Until now overrides could only be set per attribute value at generation time; a
    mistake found afterwards had to be corrected variant by variant.

    Returns (units, matched_count, skipped_inactive_count). With apply=False nothing is
    written and the units describe what would change.

    Merge semantics are deliberately narrow: for each named base line, any existing
    quantity-override or substitution row for that line is replaced, and every other row
    on the variant — including hand-added additive lines — is left alone. It cannot do
    better than that, because ProductVariantMaterial has no provenance column: a
    rule-generated row and a hand-edited one are indistinguishable. The preview is the
    answer to that, not a heuristic; the user sees each row that would be replaced and
    consents to it.
    """
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    value_attr = _resolve_attribute_slot(product, attribute_name)

    base_lines = {
        pm.material_id: pm
        for pm in (
            await session.execute(select(ProductMaterial).where(ProductMaterial.product_id == product_id))
        ).scalars()
    }
    missing = {line.base_material_id for line in lines} - set(base_lines)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material(s) {sorted(missing)} are not on this product's build BOM",
        )

    referenced = {line.base_material_id for line in lines} | {
        line.material_id for line in lines if line.material_id is not None
    }
    material_rows = (
        await session.execute(
            select(Material.id, Material.name, Material.material_type_id).where(
                Material.id.in_(referenced | set(base_lines))
            )
        )
    ).all()
    name_by_material_id = {r.id: r.name for r in material_rows}
    type_by_material_id = {r.id: r.material_type_id for r in material_rows}

    # Same strict material-type check generation applies, and for the same reason: this
    # fans out across many variants at once rather than being reviewed one at a time.
    for line in lines:
        if line.material_id is None or line.material_id == line.base_material_id:
            continue
        if type_by_material_id.get(line.material_id) != type_by_material_id.get(line.base_material_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Material {line.material_id} is not the same material type as base "
                    f"material {line.base_material_id} — can't auto-substitute across types"
                ),
            )

    variant_query = select(ProductVariant).where(
        ProductVariant.product_id == product_id,
        getattr(ProductVariant, value_attr) == attribute_value,
    )
    matched = list((await session.execute(variant_query)).scalars())
    targets = matched if include_inactive else [v for v in matched if v.is_active]
    skipped_inactive = len(matched) - len(targets)

    existing_by_variant: dict[int, list[ProductVariantMaterial]] = {}
    if targets:
        rows = (
            await session.execute(
                select(ProductVariantMaterial).where(
                    ProductVariantMaterial.variant_id.in_([v.id for v in targets])
                )
            )
        ).scalars()
        for row in rows:
            existing_by_variant.setdefault(row.variant_id, []).append(row)

    units: list = []
    unit_validation: list[tuple[int, Decimal]] = []
    for variant in targets:
        existing = existing_by_variant.get(variant.id, [])
        changes, replaced_rows, new_rows = _plan_amend(variant, existing, lines, base_lines, name_by_material_id)

        # Validate the variant's RESULTING full row set, not just the new rows — the
        # amend can collide with an override this variant already had from a different
        # attribute, which neither set would reveal alone.
        surviving = [r for r in existing if r not in replaced_rows]
        resulting = [
            ResolvedOverride(
                base_material_id=r.replaces_material_id or r.material_id,
                material_id=r.material_id,
                replaces_material_id=r.replaces_material_id,
                qty_required=Decimal(r.qty_required),
            )
            for r in surviving
        ] + new_rows
        messages = _collision_messages(resulting, set(base_lines), name_by_material_id, variant.variant_name)
        if messages:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=messages[0])

        unit_validation.extend((row.material_id, row.qty_required) for row in new_rows)
        units.append((variant, changes, replaced_rows, new_rows))

    await validate_lines_against_units(session, unit_validation, "qty_required")

    if apply:
        for _variant, _changes, replaced_rows, _new_rows in units:
            for row in replaced_rows:
                await session.delete(row)
        # Flush the deletes before inserting: a replacement reuses the same
        # (variant_id, material_id), and in a single flush SQLAlchemy orders the INSERT
        # before the DELETE, tripping the unique constraint.
        await session.flush()
        for variant, _changes, _replaced_rows, new_rows in units:
            for row in new_rows:
                session.add(row.to_row(variant.id))
        await session.commit()

    return units, len(matched), skipped_inactive


def _plan_amend(
    variant: ProductVariant,
    existing: list[ProductVariantMaterial],
    lines: list,
    base_lines: dict[int, ProductMaterial],
    name_by_material_id: dict[int, str],
) -> tuple[list, list[ProductVariantMaterial], list[ResolvedOverride]]:
    """Works out, for one variant, which existing rows the amend replaces and what it
    writes in their place. Pure — no session, no I/O — so the merge rules are testable
    directly and the caller can reuse the result for both preview and apply."""
    changes: list = []
    replaced: list[ProductVariantMaterial] = []
    new_rows: list[ResolvedOverride] = []

    for line in lines:
        base_id = line.base_material_id
        base_qty = Decimal(base_lines[base_id].qty_required)
        # A row "for this base line" is either a quantity override on the line itself or a
        # substitution away from it. Additive rows (a material not on the base BOM, with no
        # replaces_material_id) belong to neither and are never touched.
        current = next(
            (
                r
                for r in existing
                if (r.material_id == base_id and r.replaces_material_id is None)
                or r.replaces_material_id == base_id
            ),
            None,
        )
        before_material = current.material_id if current is not None else None
        before_qty = Decimal(current.qty_required) if current is not None else None

        target_material = line.material_id if line.material_id is not None else base_id
        target_qty = line.qty_required if line.qty_required is not None else base_qty

        # Same skip rule the generator uses: a result identical to the base BOM needs no
        # row at all, so the amend deletes the old one and writes nothing.
        is_noop = target_material == base_id and target_qty == base_qty

        if current is not None:
            if not is_noop and current.material_id == target_material and Decimal(current.qty_required) == target_qty:
                continue  # already exactly right — no change for this line
            replaced.append(current)

        if not is_noop:
            new_rows.append(
                ResolvedOverride(
                    base_material_id=base_id,
                    material_id=target_material,
                    replaces_material_id=base_id if target_material != base_id else None,
                    qty_required=target_qty,
                )
            )

        if current is None and is_noop:
            continue  # inherited the base BOM before, still does — nothing happened

        changes.append(
            {
                "base_material_id": base_id,
                "base_material_name": _name(name_by_material_id, base_id),
                "before_material_id": before_material,
                "before_qty": before_qty,
                "after_material_id": None if is_noop else target_material,
                "after_qty": None if is_noop else target_qty,
            }
        )

    return changes, replaced, new_rows
