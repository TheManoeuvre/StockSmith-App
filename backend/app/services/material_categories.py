"""Resolving a material category name to its reference row, and keeping the legacy column legal.

Kept out of the router so the CSV importer, the materials endpoints and the category router
itself share one definition of what a category string means — the same reasoning as
services/colours.py.

The extra job this module has, which colours did not, is `legacy_value_for`. `materials.category`
still exists and still carries a CHECK constraint listing the original seven values, so a
material assigned to a user-created category has nothing legal to put there. See that function.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.material_category import MaterialCategory

# The seven original categories, with the behaviour that used to be hardcoded against each.
# Ordered as they were on the materials page — roughly by how often they come up, which is why
# alphabetical would have been a regression.
#
# The migration that creates the table inlines its own copy of this list rather than importing
# it. That is deliberate: a migration is a record of what happened at one revision, and one that
# imports app code silently re-runs against a future definition of it.
DEFAULT_CATEGORIES: tuple[dict, ...] = (
    {
        "name": "filament",
        "sort_order": 10,
        "default_unit": MaterialUnit.g,
        "consumed_on_failed_build": True,
        "tracks_colour": True,
        "tracks_material_type": True,
        "cost_per_kg_display": True,
    },
    {"name": "resin", "sort_order": 20, "default_unit": MaterialUnit.g},
    {"name": "pigment", "sort_order": 30, "default_unit": MaterialUnit.g},
    {"name": "hardware", "sort_order": 40, "default_unit": MaterialUnit.each},
    {
        "name": "packaging",
        "sort_order": 50,
        "default_unit": MaterialUnit.each,
        "auto_kitting_per_order": True,
        "show_in_kitting_bom_list": True,
    },
    {"name": "blanks", "sort_order": 60, "default_unit": MaterialUnit.each},
    {"name": "other", "sort_order": 70, "default_unit": MaterialUnit.g},
)

_LEGACY_VALUES: frozenset[str] = frozenset(c.value for c in LegacyMaterialCategory)

# What each flag was before it was a flag, keyed by the legacy enum value. Only consulted for a
# material whose category_id is NULL — a row written by an older release mid-transition, or a
# test database built with create_all instead of migrations. Derived from DEFAULT_CATEGORIES so
# the two cannot drift.
_LEGACY_FLAGS: dict[str, dict] = {c["name"]: c for c in DEFAULT_CATEGORIES}


def legacy_value_for(name: str | None) -> LegacyMaterialCategory:
    """What to store in the legacy `materials.category` column for a category of this name.

    The column is NOT NULL and its CHECK constraint accepts exactly the original seven strings,
    so a material in a user-created category has to store something else — 'other' is the only
    honest answer available.

    The visible cost: a material filed under "Cardstock" reads back as "other" on the previous
    release, or in a backup restored into it. That is the price of not dropping the column in
    the same release that stopped depending on it, and dropping it next release is what settles
    it.
    """
    if name is not None and name in _LEGACY_VALUES:
        return LegacyMaterialCategory(name)
    return LegacyMaterialCategory.other


def category_flag(material: Material, flag: str) -> bool:
    """Read one behaviour flag off a material's category.

    Falls back to how the category behaved when it was an enum if the material has no
    `category_id` yet, so a row written by an older release keeps behaving the way it did.
    """
    if material.category_ref is not None:
        return bool(getattr(material.category_ref, flag))
    legacy = _LEGACY_FLAGS.get(material.category.value if material.category is not None else "", {})
    return bool(legacy.get(flag, False))


async def next_sort_order(session: AsyncSession) -> int:
    """One step past the last category.

    Categories can be created by a name-only form or by a CSV import naming one that doesn't
    exist yet, neither of which has any opinion about position. Without this they would all
    land on 0 and sort against each other arbitrarily.
    """
    current = (await session.execute(select(func.max(MaterialCategory.sort_order)))).scalar()
    return (current or 0) + 10


async def find_or_create(session: AsyncSession, name: str | None) -> MaterialCategory | None:
    """Match an existing category case-insensitively, or create one.

    Case-insensitivity matters more here than it did for colours. While the set was closed, a
    CSV row saying "Filament" failed loudly and the user fixed the file. Now that the set is
    open, an exact match would instead succeed by creating a *second* category alongside the
    first, quietly splitting the user's materials across two rows that mean one thing.

    Does not commit — the caller owns the transaction.
    """
    if name is None:
        return None
    cleaned = name.strip()
    if not cleaned:
        return None

    existing = (
        await session.execute(
            select(MaterialCategory).where(func.lower(MaterialCategory.name) == cleaned.lower())
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    category = MaterialCategory(name=cleaned, sort_order=await next_sort_order(session))
    session.add(category)
    await session.flush()
    return category


async def resolve_updates(session: AsyncSession, updates: dict) -> dict:
    """Turn a `category` name in a payload into a `category_id`, keeping both columns in step.

    Mirrors colours.resolve_updates, with the one difference that the legacy column cannot
    simply take the name — it has to go through `legacy_value_for`.
    """
    if updates.get("category_id") is not None:
        category = await session.get(MaterialCategory, updates["category_id"])
        updates["category"] = legacy_value_for(category.name if category else None)
        return updates

    if "category" in updates:
        category = await find_or_create(session, updates.get("category"))
        if category is None:
            # Category is required; leaving both keys as they arrived lets the schema layer
            # report it rather than writing a silent 'other'.
            return updates
        updates["category_id"] = category.id
        updates["category"] = legacy_value_for(category.name)

    return updates


async def sync_legacy_column(session: AsyncSession, category_id: int) -> None:
    """Re-derive `materials.category` for every material pointing at this category.

    Needed after a rename or a merge, both of which change which legacy value a material should
    be storing without touching the material rows themselves. Renaming "filament" to
    "Filament PLA" takes its materials out of the legacy vocabulary; merging one category into
    another moves them into a different legacy value entirely.
    """
    category = await session.get(MaterialCategory, category_id)
    if category is None:
        return
    legacy = legacy_value_for(category.name)
    materials = (
        await session.execute(select(Material).where(Material.category_id == category_id))
    ).scalars()
    changed = False
    for material in materials:
        if material.category != legacy:
            material.category = legacy
            changed = True
    if changed:
        await session.commit()
