from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProductKittingMaterial(Base):
    """Base kitting BOM: quantity of a packaging material (box, label, packing material,
    etc) required to pack and ship one unit of a product — the packaging analog of
    ProductMaterial. Never consumed by create_build(); only reserved/consumed by order
    allocation/shipping (services/kitting.py), since packaging is applied at pick/pack
    time, not build time."""

    __tablename__ = "product_kitting_materials"
    __table_args__ = (
        UniqueConstraint("product_id", "material_id", name="uq_product_kitting_materials_product_material"),
        CheckConstraint("qty_required > 0", name="ck_product_kitting_materials_qty_required_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty_required: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)

    product: Mapped["Product"] = relationship(back_populates="kitting_lines")


class ProductVariantKittingMaterial(Base):
    """Variant override for the kitting BOM — identical qty-override/substitution/additive
    semantics as ProductVariantMaterial (see that model's docstring), just for packaging."""

    __tablename__ = "product_variant_kitting_materials"
    __table_args__ = (
        UniqueConstraint(
            "variant_id", "material_id", name="uq_product_variant_kitting_materials_variant_material"
        ),
        CheckConstraint("qty_required >= 0", name="ck_product_variant_kitting_materials_qty_required_nonneg"),
        CheckConstraint(
            "replaces_material_id IS NULL OR replaces_material_id != material_id",
            name="ck_product_variant_kitting_materials_no_self_substitution",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty_required: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    replaces_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )

    variant: Mapped["ProductVariant"] = relationship(back_populates="kitting_overrides")


class OrderKittingOverride(Base):
    """Order-level override on top of the order's auto-computed aggregate kitting
    requirement (sum of every line's resolved kitting BOM x qty). Same 3-kind semantics
    as ProductVariantKittingMaterial (qty override / substitution / additive), but layered
    on the whole order's totals instead of a single product's base BOM — this is what lets
    a two-line order needing "2 labels" by default be overridden down to "1 label", or a
    default box substituted for a larger one covering the whole order.
    """

    __tablename__ = "order_kitting_overrides"
    __table_args__ = (
        UniqueConstraint("order_id", "material_id", name="uq_order_kitting_overrides_order_material"),
        CheckConstraint("qty_required >= 0", name="ck_order_kitting_overrides_qty_required_nonneg"),
        CheckConstraint(
            "replaces_material_id IS NULL OR replaces_material_id != material_id",
            name="ck_order_kitting_overrides_no_self_substitution",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty_required: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    replaces_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )


class OrderKittingAllocation(Base):
    """Ledger of packaging currently reserved/consumed for an order, one row per (order,
    material) actually touched. reconcile_order_kitting() (services/kitting.py) diffs the
    order's freshly-computed reservation/consumption targets against these rows on every
    allocate/ship/cancel/deallocate, applying only the delta to Material.allocated_qty
    (reservation) or Material.current_qty via a MaterialAdjustment (consumption at ship).

    reserved_qty and consumed_qty are independent, not a subset relationship: reserved_qty
    is a snapshot of what's currently outstanding (tracks each line's allocated-but-
    unshipped qty, so it drops back toward zero as a line ships), while consumed_qty is a
    cumulative, monotonic total of what's ever been physically consumed for this order —
    a fully-shipped line drives reserved_qty to 0 while consumed_qty stays at its full
    historical value, so there's no ordering constraint between the two.
    """

    __tablename__ = "order_kitting_allocations"
    __table_args__ = (
        UniqueConstraint("order_id", "material_id", name="uq_order_kitting_allocations_order_material"),
        CheckConstraint("reserved_qty >= 0", name="ck_order_kitting_allocations_reserved_qty_nonneg"),
        CheckConstraint("consumed_qty >= 0", name="ck_order_kitting_allocations_consumed_qty_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    reserved_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    consumed_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
