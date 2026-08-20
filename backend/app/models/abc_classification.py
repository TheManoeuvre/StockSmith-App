"""ABC classification: how often a thing is worth counting, and the cadence per tier.

This module imports nothing from the rest of the model package — both group-level
overrides key on an id via a string FK target, so neither needs the table it points at.
`MaterialCategoryABC` used to live in models/material.py for exactly that reason: it keyed
on the `MaterialCategory` enum and importing it here would have made the two modules
import each other. Categories became a lookup table, so the enum is gone from this path
and the override sits next to its products twin.
"""

import enum

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class ABCClass(str, enum.Enum):
    """How often something is worth counting. A is counted most, C least.

    Assigned by hand, not derived from sales or usage velocity — automatic tier
    calculation from order history is deliberately out of scope, so this is a preference
    the user states rather than a number the app computes.
    """

    A = "A"
    B = "B"
    C = "C"


class ABCScope(str, enum.Enum):
    """Materials and finished products are classified independently.

    Two separate baselines rather than one shared default, because the two catalogues
    have genuinely different shapes: a handful of expensive resins against a long tail of
    cheap printed items. A single default would be wrong for one of them.
    """

    material = "material"
    product = "product"


class MaterialCategoryABC(Base):
    """Tier for every material in one category — the middle of ABC's three levels.

    Keyed on the category rather than on `material_types.id`, which is the other candidate
    and the one the word "type" would suggest. The reason is coverage: the UI only ever
    sets material_type_id for filament, so hardware, packaging and blanks all have it
    NULL. Keying the tier there would mean it silently did nothing for exactly the bulk,
    count-rarely items C tier exists to serve.

    Keyed on `category_id`, not on the legacy `materials.category` enum column that still
    sits beside it. Every material in a user-created category stores 'other' there, so an
    enum-keyed tier would lump every category anyone added into a single assignment — and
    the whole point of categories becoming configurable is that there can now be more of
    them than the original seven.

    CASCADE and the absence from reference_data.REFERENCES both match ProductCategoryABC
    below, for the same reason: this is an attribute of the category, not a use of it.

    Sparse: no row means "fall through to the shop-wide material baseline".
    """

    __tablename__ = "material_category_abc"

    category_id: Mapped[int] = mapped_column(
        ForeignKey("material_categories.id", ondelete="CASCADE"), primary_key=True
    )
    abc_class: Mapped[ABCClass] = mapped_column(portable_enum(ABCClass, name="abc_class"), nullable=False)


class ProductCategoryABC(Base):
    """Tier for every product of one type — the products mirror of MaterialCategoryABC.

    CASCADE on the FK because this row is an attribute of the product category, not a record
    of anything: delete the type and its tier assignment goes with it. That is also why
    this FK is deliberately absent from services/reference_data.py's REFERENCES map — a
    tier assignment is not "usage" that should block deleting an otherwise-unused type.

    Sparse: no row means "fall through to the shop-wide product baseline".
    """

    __tablename__ = "product_category_abc"

    product_category_id: Mapped[int] = mapped_column(
        ForeignKey("product_categories.id", ondelete="CASCADE"), primary_key=True
    )
    abc_class: Mapped[ABCClass] = mapped_column(portable_enum(ABCClass, name="abc_class"), nullable=False)


class ABCTierSetting(Base):
    """How many days may pass before an item of this tier is due for counting again.

    Sparse overrides only — this table ships empty and the code defaults in
    services/abc.py win unless a row exists, the same shape platform_field_limits uses
    and argues for over seeding rows in a migration. The difference matters on upgrade:
    seeded values are a fork of the defaults from the moment they're written, so
    improving a default later reaches nobody who already ran the migration.
    """

    __tablename__ = "abc_tier_settings"
    __table_args__ = (
        UniqueConstraint("scope", "tier", name="uq_abc_tier_settings_scope_tier"),
        CheckConstraint("interval_days > 0", name="ck_abc_tier_settings_interval_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[ABCScope] = mapped_column(portable_enum(ABCScope, name="abc_scope"), nullable=False)
    tier: Mapped[ABCClass] = mapped_column(portable_enum(ABCClass, name="abc_class"), nullable=False)
    interval_days: Mapped[int] = mapped_column(nullable=False)
