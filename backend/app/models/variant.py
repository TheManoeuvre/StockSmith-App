from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("product_id", "variant_name", name="uq_product_variants_product_name"),
        UniqueConstraint(
            "product_id",
            "attribute1_value",
            "attribute2_value",
            "attribute3_value",
            name="uq_product_variants_attribute_combo",
        ),
        CheckConstraint("current_stock >= 0", name="ck_product_variants_current_stock_nonneg"),
        CheckConstraint(
            "allocated_qty >= 0 AND allocated_qty <= current_stock", name="ck_product_variants_allocated_qty_range"
        ),
        CheckConstraint("sale_price >= 0", name="ck_product_variants_sale_price_nonneg"),
        CheckConstraint(
            "platform_fee_percent >= 0 AND platform_fee_percent <= 100",
            name="ck_product_variants_platform_fee_percent_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_name: Mapped[str] = mapped_column(String, nullable=False)
    sku_suffix: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    current_stock: Mapped[int] = mapped_column(default=0, nullable=False)
    allocated_qty: Mapped[int] = mapped_column(default=0, nullable=False)
    attribute1_value: Mapped[str | None] = mapped_column(String, nullable=True)
    attribute2_value: Mapped[str | None] = mapped_column(String, nullable=True)
    attribute3_value: Mapped[str | None] = mapped_column(String, nullable=True)
    # NULL means "falls back to the product's own price" — only meaningful when the
    # product's pricing_mode is "variable" or "line" (see models/product.py:PricingMode).
    sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    shipping_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("shipping_profiles.id", ondelete="SET NULL"), nullable=True
    )
    platform_fee_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bom_overrides: Mapped[list["ProductVariantMaterial"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )
    kitting_overrides: Mapped[list["ProductVariantKittingMaterial"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )


class ProductVariantMaterial(Base):
    """BOM override for a variant. Absence of a row means 'inherit the base product BOM qty'.

    A row with qty_required=0 means the variant genuinely needs none of that material —
    distinct from "no override", which is why this is a separate table rather than a
    nullable column merged onto product_materials.

    Three row kinds share this table, distinguished by replaces_material_id:
    - qty override: material_id matches an existing base BOM line, replaces_material_id NULL.
    - substitution: material_id is the new material, replaces_material_id is the base
      line's original material_id (which is dropped from the effective BOM entirely).
    - additive extra line: material_id not in the base BOM, replaces_material_id NULL.
    """

    __tablename__ = "product_variant_materials"
    __table_args__ = (
        UniqueConstraint("variant_id", "material_id", name="uq_product_variant_materials_variant_material"),
        CheckConstraint("qty_required >= 0", name="ck_product_variant_materials_qty_required_nonneg"),
        CheckConstraint(
            "replaces_material_id IS NULL OR replaces_material_id != material_id",
            name="ck_product_variant_materials_no_self_substitution",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty_required: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    replaces_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )

    variant: Mapped["ProductVariant"] = relationship(back_populates="bom_overrides")
