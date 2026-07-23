import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum


class MaterialCategory(str, enum.Enum):
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
    category: Mapped[MaterialCategory] = mapped_column(
        portable_enum(MaterialCategory, name="material_category"), nullable=False
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
    colour: Mapped[str | None] = mapped_column(String, nullable=True)
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    purchase_lines: Mapped[list["MaterialPurchase"]] = relationship(back_populates="material")
    adjustments: Mapped[list["MaterialAdjustment"]] = relationship(back_populates="material")
    manufacturer: Mapped["Manufacturer | None"] = relationship()
    default_supplier: Mapped["Supplier | None"] = relationship()
    material_type: Mapped["MaterialType | None"] = relationship()

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
