from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class MaterialSubstitute(Base):
    """A human-curated fallback: "substitute_material can stand in for material when
    material runs short." Declared once here, it applies at every product/BOM/kitting
    line that references `material` — unlike ProductVariantMaterial.replaces_material_id
    (and its ProductVariantKittingMaterial/OrderKittingOverride siblings), which is a
    one-off swap that has to be re-declared separately on every product using the
    original material. That older mechanism is untouched by this table and stays exactly
    what it was: an explicit per-variant/per-order override, not a material-level policy.

    Deliberately NOT restricted to matching material_category, unlike
    routers.variants._validate_substitution_categories: that same-category rule exists
    for the narrower per-BOM-line feature (whose frontend dropdown already filters to
    "same shelf"), and doesn't apply here. A person may judge a material in a different
    category a perfectly good stand-in for another, and this table exists precisely to
    record that judgment, not to second-guess it by category.

    Substitutes are curated, never computed: nothing here infers a match from size,
    dimension, or material type, and no such column should ever be added — a person
    decides "Y is a suitable stand-in for X" and this row is just where that decision is
    written down. The shortage consumers (buildability lines, kitting lines, orders
    awaiting packaging) only ever *suggest* a listed substitute; nothing auto-applies one.
    Build and packaging capacity (services/material_substitutes.FALLBACK_POOL_BY_MATERIAL_SQL)
    do count an active fallback's free stock as available for the material it backs — so a
    short material with a well-stocked fallback doesn't cap a product's sellable figure —
    but report it beside the material-only figure rather than in place of it. That's a
    number, not an allocation; which material actually gets used is still chosen at
    build/pack time.

    rank orders multiple fallbacks for the same material — lower tried/offered first.
    is_active soft-disables a fallback without losing its history, matching how
    Material.is_active itself is a soft delete rather than a row removal.
    """

    __tablename__ = "material_substitutes"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "substitute_material_id", name="uq_material_substitutes_material_substitute"
        ),
        CheckConstraint(
            "substitute_material_id != material_id", name="ck_material_substitutes_no_self_substitution"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    substitute_material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    # Ascending; ties broken by id (insertion order) — same convention as
    # MaterialCategory.sort_order.
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Why this is a valid substitute — e.g. "close enough filament proxy, same diameter
    # spool" — free text because that judgment doesn't decompose into structured fields
    # without becoming exactly the size/compatibility inference this feature deliberately
    # avoids computing.
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # Free text, not a foreign key: the app has no per-user identity (a single shared
    # password gates every request — see deps.require_auth), so this simply records
    # whatever the caller identifies itself as, mirroring how the rest of the app has no
    # "created_by" concept to point at instead.
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped["Material"] = relationship(foreign_keys=[material_id])
    substitute_material: Mapped["Material"] = relationship(foreign_keys=[substitute_material_id])


class MaterialSubstituteUsage(Base):
    """Audit trail for a person actually choosing to use a listed substitute — the
    material-substitution analog of MaterialAdjustment (see that model's docstring):
    records which material was substituted, which material it was substituted with, for
    which order/build, and by whom, so the choice is traceable later.

    Deliberately separate from MaterialAdjustment rather than shoehorned into it: choosing
    a substitute is not itself a quantity correction on `material` (the row that was
    short) — the actual stock movement belongs to whatever consumed substitute_material
    (a build's own MaterialAdjustment, a kitting consumption), recorded independently by
    those flows exactly as it already is for any other material. This row exists purely to
    say "and a listed substitute was used here instead of the original", so a later reader
    can see why substitute_material's stock moved for this order/build.
    """

    __tablename__ = "material_substitute_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    substitute_material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
    )
    qty: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped["Material"] = relationship(foreign_keys=[material_id])
    substitute_material: Mapped["Material"] = relationship(foreign_keys=[substitute_material_id])
