import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.abc_classification import ABCClass
from app.models.base import Base, portable_enum


class LegacyMaterialCategory(str, enum.Enum):
    """The original fixed category list, superseded by the `material_categories` table.

    Kept only because `materials.category` is still written and still carries a CHECK
    constraint listing exactly these seven values — see the column's comment below. Read
    categories through `Material.category_name`; the only code that should touch this enum is
    the code keeping the legacy column legal.
    """

    filament = "filament"
    resin = "resin"
    pigment = "pigment"
    hardware = "hardware"
    packaging = "packaging"
    blanks = "blanks"
    other = "other"


class MaterialUnit(str, enum.Enum):
    g = "g"
    ml = "ml"
    each = "each"


class MaterialAdjustmentMode(str, enum.Enum):
    adjust = "adjust"
    set = "set"


class Material(Base):
    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint("current_qty >= 0", name="ck_materials_current_qty_nonneg"),
        CheckConstraint("reorder_threshold >= 0", name="ck_materials_reorder_threshold_nonneg"),
        CheckConstraint("avg_unit_cost >= 0", name="ck_materials_avg_unit_cost_nonneg"),
        CheckConstraint(
            "allocated_qty >= 0 AND allocated_qty <= current_qty", name="ck_materials_allocated_qty_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # The legacy category column, kept in step with category_id during the transition and
    # deliberately not dropped yet — same reasoning as `colour` below. Its CHECK constraint is
    # why a material in a user-created category has to store 'other' here for now; see
    # services/material_categories.legacy_value_for. Read through `category_name`; write both.
    category: Mapped[LegacyMaterialCategory] = mapped_column(
        portable_enum(LegacyMaterialCategory, name="material_category"), nullable=False
    )
    # RESTRICT rather than the SET NULL every other reference FK uses. A material with no
    # manufacturer is untidy; a material with no category is a state the app does not model —
    # the list groups by it, the substitution validator compares it, the create form requires
    # it. Better for the database to refuse than to quietly blank it. Merge is unaffected: it
    # repoints before it deletes.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_categories.id", ondelete="RESTRICT"), nullable=True
    )
    unit: Mapped[MaterialUnit] = mapped_column(
        portable_enum(MaterialUnit, name="material_unit"), nullable=False
    )
    current_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    # Soft-reserved by kitting-BOM allocation (services/kitting.py) — mirrors
    # Product.allocated_qty. Only ever moved by order allocate/ship/cancel reconciliation,
    # never by recompute_material(), which only ever touches current_qty/avg_unit_cost.
    allocated_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    reorder_threshold: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    avg_unit_cost: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Filament-specific in the UI (only shown/edited when category=filament), but not
    # enforced at the DB level — plain nullable metadata usable by any category.
    #
    # `colour` is the legacy free-text column, kept in step with colour_id during the transition
    # and deliberately not dropped yet: SQLite needs a table rebuild to drop a column, and now
    # that restoring an older backup and migrating it forward is a routine operation, one-way
    # changes want to wait a release. Read through `colour_name`; write both.
    colour: Mapped[str | None] = mapped_column(String, nullable=True)
    colour_id: Mapped[int | None] = mapped_column(ForeignKey("colours.id", ondelete="SET NULL"), nullable=True)
    material_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_types.id", ondelete="SET NULL"), nullable=True
    )
    barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    manufacturer_id: Mapped[int | None] = mapped_column(
        ForeignKey("manufacturers.id", ondelete="SET NULL"), nullable=True
    )
    default_supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    typical_reorder_qty: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    product_url: Mapped[str | None] = mapped_column(String, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    image_original_filename: Mapped[str | None] = mapped_column(String, nullable=True)

    # Stock-take classification. Both nullable, and NULL means "inherit" rather than
    # "unset" — abc_class falls through to the category's tier and then the shop-wide
    # material baseline, stock_take_interval_days to the resolved tier's cadence. See
    # services/abc.py for the resolution order; nothing else should reimplement it.
    abc_class: Mapped["ABCClass | None"] = mapped_column(
        portable_enum(ABCClass, name="abc_class"), nullable=True
    )
    stock_take_interval_days: Mapped[int | None] = mapped_column(nullable=True)
    # Set only when a stock take was approved *and* a count was actually entered for this
    # material — a line left blank means "assume the system is right", which is not the
    # same as having counted it, and must not reset the clock. NULL reads as "never
    # counted" and sorts first in the due-for-counting list.
    last_stock_take_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL means "counted outside a take" — a hand-made Set adjustment sets the date above
    # but belongs to no take — as well as "never counted". The date is what the cadence
    # reads; this only says where it came from.
    last_stock_take_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_takes.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    purchase_lines: Mapped[list["MaterialPurchase"]] = relationship(back_populates="material")
    adjustments: Mapped[list["MaterialAdjustment"]] = relationship(back_populates="material")
    manufacturer: Mapped["Manufacturer | None"] = relationship()
    default_supplier: Mapped["Supplier | None"] = relationship()
    material_type: Mapped["MaterialType | None"] = relationship()
    colour_ref: Mapped["Colour | None"] = relationship()
    # Eagerly loaded, unlike its siblings. Several services select materials with no options at
    # all and then read the category to decide behaviour; under the async session a lazy load
    # there raises MissingGreenlet at runtime while passing in tests that happen to have the row
    # in the identity map. The table holds a handful of rows, so the extra query is noise.
    category_ref: Mapped["MaterialCategory | None"] = relationship(lazy="selectin")

    @property
    def category_name(self) -> str | None:
        """Prefers the reference row, falling back to the legacy column.

        The fallback covers rows written by an older release during the transition, and the
        test suite, which builds its schema with create_all rather than running migrations.
        """
        if self.category_ref is not None:
            return self.category_ref.name
        return self.category.value if self.category is not None else None

    @property
    def colour_name(self) -> str | None:
        """Prefers the reference row, falling back to the legacy column.

        The fallback matters during the transition and for any row a migration couldn't resolve
        — a material keeps showing its colour either way.
        """
        if self.colour_ref is not None:
            return self.colour_ref.name
        return self.colour

    @property
    def manufacturer_name(self) -> str | None:
        return self.manufacturer.name if self.manufacturer else None

    @property
    def default_supplier_name(self) -> str | None:
        return self.default_supplier.name if self.default_supplier else None

    @property
    def material_type_name(self) -> str | None:
        return self.material_type.name if self.material_type else None


class MaterialAdjustment(Base):
    """Audit trail for manual qty corrections (breakage, physical recount, etc).

    Never affects avg_unit_cost — only received purchases contribute new cost basis.
    Replayed in chronological order by recompute_material() alongside received
    purchase lines to rebuild a material's current_qty/avg_unit_cost from scratch —
    replay only ever reads qty_delta, regardless of mode.

    mode/target_qty exist purely for audit display: a "set" adjustment (a physical
    stock count) is still stored as the plain delta needed to reach that count, but
    target_qty remembers the count itself so history can show "Set to 53" instead of
    a bare "+12". A "set" adjustment confirming the count already matches produces a
    zero delta, which is why the nonzero constraint is relaxed for mode='set'.
    """

    __tablename__ = "material_adjustments"
    __table_args__ = (
        CheckConstraint("qty_delta != 0 OR mode = 'set'", name="ck_material_adjustments_qty_delta_nonzero"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[MaterialAdjustmentMode] = mapped_column(
        portable_enum(MaterialAdjustmentMode, name="material_adjustment_mode"),
        nullable=False,
        default=MaterialAdjustmentMode.adjust,
    )
    qty_delta: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    target_qty: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    material: Mapped["Material"] = relationship(back_populates="adjustments")
