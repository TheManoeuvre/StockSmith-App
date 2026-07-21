import re
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
) -> dict[int, ProductMaterial]:
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
        return {}

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
    if candidate_material_ids:
        result = await session.execute(
            select(Material.id, Material.material_type_id).where(
                Material.id.in_(candidate_material_ids | base_material_ids)
            )
        )
        type_by_material_id = dict(result.all())
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

    return base_lines_by_material_id


def _resolve_line_overrides(
    combo: tuple[str, ...],
    attributes: list[VariantAttributeSpec],
    base_lines_by_material_id: dict[int, ProductMaterial],
) -> list[ProductVariantMaterial]:
    """Merges every material/quantity rule that fires for this specific attribute-value
    combo into at most one effective (material_id, qty_required) per base BOM line —
    e.g. Colour substituting the material and Size overriding the qty on the SAME base
    line both land on one row, not two conflicting ones (the override table has a unique
    constraint on (variant_id, material_id))."""
    effective: dict[int, list] = {}  # base_material_id -> [effective_material_id, effective_qty | None]
    for i, spec in enumerate(attributes):
        value = combo[i]
        for rule in spec.material_rules:
            if value in rule.value_to_material_id:
                entry = effective.setdefault(rule.base_material_id, [rule.base_material_id, None])
                entry[0] = rule.value_to_material_id[value]
        for rule in spec.quantity_rules:
            if value in rule.value_to_qty:
                entry = effective.setdefault(rule.base_material_id, [rule.base_material_id, None])
                entry[1] = rule.value_to_qty[value]

    rows: list[ProductVariantMaterial] = []
    for base_id, (material_id, qty) in effective.items():
        base_line = base_lines_by_material_id[base_id]
        base_qty = Decimal(base_line.qty_required)
        final_qty = qty if qty is not None else base_qty
        if material_id == base_id and final_qty == base_qty:
            continue  # no actual change from the base BOM — nothing to write
        rows.append(
            ProductVariantMaterial(
                material_id=material_id,
                replaces_material_id=base_id if material_id != base_id else None,
                qty_required=final_qty,
            )
        )
    return rows


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

    base_lines_by_material_id = await _validate_attribute_rules(session, product_id, attributes)

    names = [spec.name for spec in attributes] + [None] * (3 - len(attributes))
    product.variant_attribute1_name, product.variant_attribute2_name, product.variant_attribute3_name = names

    result = await session.execute(select(ProductVariant).where(ProductVariant.product_id == product_id))
    existing_combos = {
        (v.attribute1_value, v.attribute2_value, v.attribute3_value) for v in result.scalars()
    }

    created: list[ProductVariant] = []
    for combo in cartesian_product(*(spec.values for spec in attributes)):
        padded = list(combo) + [None] * (3 - len(combo))
        if tuple(padded) in existing_combos:
            continue
        variant = ProductVariant(
            product_id=product_id,
            variant_name=" / ".join(combo),
            sku_suffix="-".join(slugify(v) for v in combo),
            attribute1_value=padded[0],
            attribute2_value=padded[1],
            attribute3_value=padded[2],
        )
        session.add(variant)
        await session.flush()  # assigns variant.id, needed for the override rows below
        created.append(variant)

        for override in _resolve_line_overrides(combo, attributes, base_lines_by_material_id):
            override.variant_id = variant.id
            session.add(override)

    await session.commit()
    for variant in created:
        await session.refresh(variant)
    return created
