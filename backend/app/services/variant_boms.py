"""Writing a variant's BOM overrides, shared by the variant editor (routers/variants.py) and the
BOM CSV import (services/catalogue_csv.py), so a file can't get past a rule the editor enforces.

Moved here unchanged from routers/variants.py, whose kitting-override endpoint still uses the
category check and the two source helpers.
"""

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.product import ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.kitting import VariantKittingBomLine
from app.schemas.variant import VariantBomLine
from app.services import listing_push
from app.services.validation import validate_lines_against_units


async def _validate_substitution_categories(
    session: AsyncSession, payload: list[VariantBomLine] | list[VariantKittingBomLine]
) -> None:
    """A substitution should only ever swap within the same material category (filament
    for filament, packaging for packaging, etc) — the frontend's substitute dropdown
    already filters to this, but a stale build or a direct API call could still submit a
    cross-category swap, so it's enforced here too.

    "Same category" is the one rule here that could not become a flag on the category row:
    it is a relation between two categories, not a property of either. So it stays an
    equality test, now on category_id.

    One consequence worth knowing about, since categories are editable now: splitting a
    category in two makes any existing substitution across the split illegal. Validation only
    runs on write, so stored BOMs are not retroactively broken — but the next save of one
    returns a 400 rather than silently accepting what is now a cross-category swap.
    """
    material_ids = {l.material_id for l in payload} | {
        l.replaces_material_id for l in payload if l.replaces_material_id is not None
    }
    if not material_ids:
        return
    categories_by_id = dict(
        (await session.execute(select(Material.id, Material.category_id).where(Material.id.in_(material_ids)))).all()
    )
    names_by_id = dict(
        (
            await session.execute(
                select(MaterialCategory.id, MaterialCategory.name).where(
                    MaterialCategory.id.in_({c for c in categories_by_id.values() if c is not None})
                )
            )
        ).all()
    )
    for line in payload:
        if line.replaces_material_id is None:
            continue
        base_category = categories_by_id.get(line.replaces_material_id)
        sub_category = categories_by_id.get(line.material_id)
        if base_category is not None and sub_category is not None and base_category != sub_category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Material {line.material_id} ({names_by_id.get(sub_category, '?')}) cannot substitute "
                    f"material {line.replaces_material_id} ({names_by_id.get(base_category, '?')}) "
                    f"because their categories must match"
                ),
            )


async def _existing_sources(session: AsyncSession, row_cls, variant_id: int) -> dict[tuple[int, int | None], tuple[Decimal, str]]:
    """(material_id, replaces_material_id) -> (qty_required, source) for a variant's
    current override rows. The editor PUTs the variant's whole list on every save, so
    without this every row would look hand-made; a row that comes back unchanged keeps
    the source it had."""
    rows = (await session.execute(select(row_cls).where(row_cls.variant_id == variant_id))).scalars()
    return {(r.material_id, r.replaces_material_id): (Decimal(r.qty_required), r.source) for r in rows}


def _source_for(line, existing: dict[tuple[int, int | None], tuple[Decimal, str]]) -> str:
    """"manual" for a line that is new or whose quantity the user changed, else whatever
    it was before."""
    previous = existing.get((line.material_id, line.replaces_material_id))
    if previous is not None and previous[0] == Decimal(line.qty_required):
        return previous[1]
    return "manual"


async def apply_variant_bom_overrides(
    session: AsyncSession, variant: ProductVariant, payload: list[VariantBomLine]
) -> None:
    """Validate and replace a variant's whole override list, then commit. Raises HTTPException
    (400) on a rule break, before anything is written."""
    base_material_ids = set(
        (
            await session.execute(
                select(ProductMaterial.material_id).where(ProductMaterial.product_id == variant.product_id)
            )
        ).scalars()
    )

    substituted: set[int] = set()
    overridden_materials: set[int] = set()
    for line in payload:
        if line.material_id == line.replaces_material_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Material {line.material_id} cannot substitute itself",
            )
        if line.replaces_material_id is not None:
            if line.replaces_material_id not in base_material_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is not part of the base BOM",
                )
            if line.replaces_material_id in substituted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Material {line.replaces_material_id} is substituted more than once",
                )
            substituted.add(line.replaces_material_id)
        else:
            overridden_materials.add(line.material_id)

    conflicts = substituted & overridden_materials
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Material {next(iter(conflicts))} has both an override and a substitution",
        )
    await _validate_substitution_categories(session, payload)

    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )

    existing = await _existing_sources(session, ProductVariantMaterial, variant.id)
    await session.execute(delete(ProductVariantMaterial).where(ProductVariantMaterial.variant_id == variant.id))
    overrides = [
        ProductVariantMaterial(
            variant_id=variant.id,
            material_id=l.material_id,
            qty_required=l.qty_required,
            replaces_material_id=l.replaces_material_id,
            source=_source_for(l, existing),
        )
        for l in payload
    ]
    session.add_all(overrides)
    await session.commit()
    # A BOM change moves max_buildable, which moves theoretical_max_sellable, which is the
    # quantity pushed to the marketplace. Without this the corrected number sat locally
    # until some unrelated stock movement happened to trigger a push.
    listing_push.enqueue_for_product(variant.product_id, variant.id)
