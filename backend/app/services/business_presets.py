"""First-launch business presets — "what do you make?".

A fresh install is asked once which kind of business it is, and the answer decides the material
categories, units and example material types it starts with. The seven categories the app has
always seeded are 3D-print shaped (filament, resin, pigment); to a candle maker or a jeweller they
are noise to delete before the app makes sense. See docs/plan-onboarding.md §4.

The choice is recorded in `general_settings.business_type`. NULL means "not chosen yet", which is
what the frontend keys the picker on. Installs that predate this already have their data, and the
migration that added the column marks them as 3D printing so they are never asked.

Applying a preset *replaces* the categories, so it is refused once any material exists — at that
point the categories are in use and the user's own. Units and material types are only ever added,
never removed, because a fresh database can already hold ones the user typed in.

Category names are lowercase to match the original seven; the UI capitalises them for display.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.material_type import MaterialType
from app.models.material_unit import MaterialUnitRow
from app.services.general_settings import get_general_settings
from app.services.material_categories import DEFAULT_CATEGORIES
from app.services.units import DEFAULT_UNITS

# Shorthands for the category behaviour flags, so each preset reads as a list of intentions rather
# than a wall of booleans. See app/models/material_category.py for what each flag does.
_PACKAGING = {"auto_kitting_per_order": True, "show_in_kitting_bom_list": True}
_CONSUMED = {"consumed_on_failed_build": True}
_COLOUR = {"tracks_colour": True}
_TYPE = {"tracks_material_type": True}


def _cat(name: str, unit: str, *flags: dict) -> dict:
    row: dict = {"name": name, "default_unit": unit}
    for f in flags:
        row.update(f)
    return row


@dataclass(frozen=True)
class BusinessPreset:
    key: str
    label: str
    description: str
    # sort_order is assigned from list position, so these are written in display order.
    categories: tuple[dict, ...]
    # Units the categories need beyond g / ml / each: name, decimal_places, show_suffix.
    extra_units: tuple[dict, ...] = ()
    # Examples, deletable. Material type is what groups substitutable materials, and an example
    # teaches that faster than help text does.
    material_types: tuple[str, ...] = field(default_factory=tuple)


_METRES = {"name": "m", "decimal_places": 2, "show_suffix": True}
_CENTIMETRES = {"name": "cm", "decimal_places": 1, "show_suffix": True}


PRESETS: tuple[BusinessPreset, ...] = (
    BusinessPreset(
        key="3d_printing",
        label="3D printing & resin",
        description="Printed models, figures, homeware and parts — filament or resin.",
        # Exactly the seven the app has always seeded, so existing behaviour is unchanged.
        categories=tuple({k: v for k, v in c.items() if k != "sort_order"} for c in DEFAULT_CATEGORIES),
        material_types=("PLA", "PETG", "ABS", "ASA", "TPU"),
    ),
    BusinessPreset(
        key="candles_soap",
        label="Candles, soap & bath",
        description="Candles, wax melts, soap, bath bombs and skincare.",
        categories=(
            _cat("wax", "g", _CONSUMED, _TYPE, {"cost_per_kg_display": True}),
            _cat("soap base", "g", _CONSUMED, _TYPE, {"cost_per_kg_display": True}),
            _cat("fragrance", "ml", _CONSUMED),
            _cat("colourants & additives", "g", _CONSUMED),
            _cat("wicks", "each"),
            _cat("vessels", "each", _COLOUR),
            _cat("labels", "each"),
            _cat("packaging", "each", _PACKAGING),
            _cat("other", "g"),
        ),
        material_types=(
            "Soy wax",
            "Rapeseed wax",
            "Coconut wax",
            "Paraffin wax",
            "Beeswax",
            "Melt & pour (clear)",
            "Melt & pour (white)",
        ),
    ),
    BusinessPreset(
        key="jewellery",
        label="Jewellery & beading",
        description="Necklaces, earrings, bracelets and charms — metal, beads or clay.",
        categories=(
            _cat("findings", "each", _TYPE),
            _cat("beads", "each", _COLOUR, _TYPE),
            _cat("chain & wire", "cm", _CONSUMED, _TYPE),
            _cat("stones & charms", "each", _COLOUR),
            _cat("cord & thread", "cm", _CONSUMED, _COLOUR),
            _cat("packaging", "each", _PACKAGING),
            _cat("other", "each"),
        ),
        extra_units=(_CENTIMETRES,),
        material_types=("Sterling silver", "Gold filled", "Gold plated", "Brass", "Stainless steel", "Glass", "Gemstone"),
    ),
    BusinessPreset(
        key="textiles",
        label="Sewing, knitting & textiles",
        description="Clothing, bags, quilts, knitwear and embroidery.",
        categories=(
            _cat("fabric", "m", _CONSUMED, _COLOUR, _TYPE),
            _cat("yarn", "g", _CONSUMED, _COLOUR, _TYPE),
            _cat("thread", "each", _COLOUR),
            _cat("interfacing & wadding", "m", _CONSUMED),
            _cat("notions", "each", _COLOUR),
            _cat("labels & tags", "each"),
            _cat("packaging", "each", _PACKAGING),
            _cat("other", "each"),
        ),
        extra_units=(_METRES,),
        material_types=("Cotton", "Linen", "Wool", "Acrylic", "Polyester"),
    ),
    BusinessPreset(
        key="paper_print",
        label="Paper goods & print",
        description="Greetings cards, art prints, stickers, stationery and invitations.",
        categories=(
            _cat("paper & card", "each", _CONSUMED, _COLOUR, _TYPE),
            _cat("sticker & vinyl sheets", "each", _CONSUMED, _TYPE),
            _cat("envelopes", "each", _COLOUR),
            _cat("embellishments", "each", _COLOUR),
            _cat("packaging", "each", _PACKAGING),
            _cat("other", "each"),
        ),
        material_types=(
            "300gsm card",
            "Kraft card",
            "Watercolour paper",
            "Matte sticker paper",
            "Gloss sticker paper",
            "Clear vinyl",
        ),
    ),
    # The fallback, not one of the five: public distribution means someone will make something
    # none of them fit, and they need a sensible place to start rather than a wrong preset.
    BusinessPreset(
        key="general",
        label="Something else",
        description="A simple starting set for any kind of making. Add your own categories as you go.",
        categories=(
            _cat("raw materials", "g", _CONSUMED),
            _cat("components", "each"),
            _cat("consumables", "each", _CONSUMED),
            _cat("packaging", "each", _PACKAGING),
            _cat("other", "each"),
        ),
    ),
)

PRESETS_BY_KEY: dict[str, BusinessPreset] = {p.key: p for p in PRESETS}

# What the migration stamps onto installs that predate the picker.
EXISTING_INSTALL_BUSINESS_TYPE = "3d_printing"


class PresetRefused(Exception):
    """The preset can't be applied — already chosen, or materials already exist."""


async def apply_preset(session: AsyncSession, key: str) -> None:
    preset = PRESETS_BY_KEY.get(key)
    if preset is None:
        raise KeyError(key)

    settings = await get_general_settings(session)
    if settings.business_type is not None:
        raise PresetRefused("A business type has already been chosen for this install.")
    material_count = await session.scalar(select(func.count()).select_from(Material))
    if material_count:
        raise PresetRefused("Materials already exist, so their categories can't be replaced.")

    # Units first: categories name their default unit, and every name they use must exist.
    existing_units = set((await session.scalars(select(MaterialUnitRow.name))).all())
    next_unit_order = max((u["sort_order"] for u in DEFAULT_UNITS), default=0)
    for unit in preset.extra_units:
        if unit["name"] in existing_units:
            continue
        next_unit_order += 10
        session.add(MaterialUnitRow(sort_order=next_unit_order, **unit))

    # No material references a category (checked above), so replacing them is safe. Per-category
    # ABC tiers cascade with them.
    await session.execute(delete(MaterialCategory))
    for position, row in enumerate(preset.categories, start=1):
        session.add(MaterialCategory(sort_order=position * 10, **row))

    existing_types = {n.casefold() for n in (await session.scalars(select(MaterialType.name))).all()}
    for name in preset.material_types:
        if name.casefold() not in existing_types:
            session.add(MaterialType(name=name))

    settings.business_type = preset.key
    await session.commit()
