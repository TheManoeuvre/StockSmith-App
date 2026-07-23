import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum


class PricingMode(str, enum.Enum):
    """How a product's variants get their sale price:
    - product: every variant uses the product's own sale_price (today's behaviour).
    - variable: variants are grouped by one attribute (pricing_variable_attribute,
      1/2/3) and share a price per distinct value of that attribute — e.g. every
      "Large" variant prices the same regardless of colour.
    - line: every variant is priced independently.

    Both variable and line modes are backed by the same per-variant sale_price/
    shipping_profile_id/platform_fee_percent columns on ProductVariant — "variable" is just a
    UI convenience that writes the same value to every variant sharing an attribute
    value. This is why switching from variable to line retains the per-variant values
    already set: nothing is migrated, only how they're grouped for editing changes.
    """

    product = "product"
    variable = "variable"
    line = "line"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("current_stock >= 0", name="ck_products_current_stock_nonneg"),
        CheckConstraint(
            "allocated_qty >= 0 AND allocated_qty <= current_stock", name="ck_products_allocated_qty_range"
        ),
        CheckConstraint("sale_price >= 0", name="ck_products_sale_price_nonneg"),
        CheckConstraint(
            "platform_fee_percent >= 0 AND platform_fee_percent <= 100", name="ck_products_platform_fee_percent_range"
        ),
        CheckConstraint(
            "pricing_variable_attribute IS NULL OR pricing_variable_attribute BETWEEN 1 AND 3",
            name="ck_products_pricing_variable_attribute_range",
        ),
        CheckConstraint(
            "platform_ceiling_qty IS NULL OR platform_ceiling_qty >= 0",
            name="ck_products_platform_ceiling_qty_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    barcode: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    current_stock: Mapped[int] = mapped_column(default=0, nullable=False)
    allocated_qty: Mapped[int] = mapped_column(default=0, nullable=False)
    is_bundle: Mapped[bool] = mapped_column(default=False, nullable=False)
    variant_attribute1_name: Mapped[str | None] = mapped_column(String, nullable=True)
    variant_attribute2_name: Mapped[str | None] = mapped_column(String, nullable=True)
    variant_attribute3_name: Mapped[str | None] = mapped_column(String, nullable=True)
    sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    shipping_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("shipping_profiles.id", ondelete="SET NULL"), nullable=True
    )
    platform_fee_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    pricing_mode: Mapped[PricingMode] = mapped_column(
        portable_enum(PricingMode, name="pricing_mode"), nullable=False, default=PricingMode.product
    )
    pricing_variable_attribute: Mapped[int | None] = mapped_column(nullable=True)
    # Manual cap on what quantity to advertise on a marketplace, applied uniformly to
    # every variant's own max_sellable/expected_max_sellable (services/kitting.py::
    # compute_max_sellable) — independent of and on top of stock/packaging capacity.
    platform_ceiling_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bom_lines: Mapped[list["ProductMaterial"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    kitting_lines: Mapped[list["ProductKittingMaterial"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductBundleItem(Base):
    """A component product (with qty) inside a bundle/kit product.

    Buildability for a bundle is based on components' current_stock (actual on-hand
    finished goods), not their theoretical max_buildable — a bundle ships components
    you've already built, not ones you could build. See compute_bundle_buildable.
    """

    __tablename__ = "product_bundle_items"
    __table_args__ = (
        UniqueConstraint("bundle_product_id", "component_product_id", name="uq_product_bundle_items_pair"),
        CheckConstraint("qty > 0", name="ck_product_bundle_items_qty_positive"),
        CheckConstraint("bundle_product_id != component_product_id", name="ck_product_bundle_items_no_self_reference"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bundle_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    component_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    qty: Mapped[int] = mapped_column(nullable=False)


class ProductMaterial(Base):
    """Base BOM: quantity of a material required to build one unit of a product."""

    __tablename__ = "product_materials"
    __table_args__ = (
        UniqueConstraint("product_id", "material_id", name="uq_product_materials_product_material"),
        CheckConstraint("qty_required > 0", name="ck_product_materials_qty_required_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty_required: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)

    product: Mapped["Product"] = relationship(back_populates="bom_lines")
