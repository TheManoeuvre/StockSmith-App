"""initial schema

Revision ID: 2062e429185a
Revises:
Create Date: 2026-07-07 08:09:43.119313

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2062e429185a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


material_category = sa.Enum(
    "filament", "resin", "pigment", "hardware", "packaging", "other", name="material_category"
)
material_unit = sa.Enum("g", "ml", "each", name="material_unit")
asset_type = sa.Enum(
    "main_image", "listing_image", "step", "threemf", "gcode", name="asset_type"
)
listing_platform = sa.Enum("etsy", "ebay", "shopify", name="listing_platform")


def upgrade() -> None:
    """Upgrade schema."""
    # Each enum below is used by exactly one table, so it's created automatically as part
    # of that table's CREATE TABLE DDL — no need (and no ability, without hitting a
    # DuplicateObjectError) to also pre-create it explicitly here.

    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("category", material_category, nullable=False),
        sa.Column("unit", material_unit, nullable=False),
        sa.Column("current_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("reorder_threshold", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("avg_unit_cost", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("current_qty >= 0", name="ck_materials_current_qty_nonneg"),
        sa.CheckConstraint("reorder_threshold >= 0", name="ck_materials_reorder_threshold_nonneg"),
        sa.CheckConstraint("avg_unit_cost >= 0", name="ck_materials_avg_unit_cost_nonneg"),
    )

    op.create_table(
        "material_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id",
            sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("purchase_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("supplier", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0", name="ck_material_purchases_qty_positive"),
        sa.CheckConstraint("total_cost >= 0", name="ck_material_purchases_total_cost_nonneg"),
    )
    op.create_index("ix_material_purchases_material_id", "material_purchases", ["material_id"])
    op.create_index("ix_material_purchases_date", "material_purchases", ["purchase_date"])

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sku", sa.String(), nullable=True, unique=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "product_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("qty_required", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("product_id", "material_id", name="uq_product_materials_product_material"),
        sa.CheckConstraint("qty_required > 0", name="ck_product_materials_qty_required_positive"),
    )
    op.create_index("ix_product_materials_product_id", "product_materials", ["product_id"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("variant_name", sa.String(), nullable=False),
        sa.Column("sku_suffix", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("product_id", "variant_name", name="uq_product_variants_product_name"),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])

    op.create_table(
        "product_variant_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("qty_required", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint(
            "variant_id", "material_id", name="uq_product_variant_materials_variant_material"
        ),
        sa.CheckConstraint("qty_required >= 0", name="ck_product_variant_materials_qty_required_nonneg"),
    )
    op.create_index(
        "ix_product_variant_materials_variant_id", "product_variant_materials", ["variant_id"]
    )

    op.create_table(
        "product_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("asset_type", asset_type, nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_product_assets_product_id", "product_assets", ["product_id"])
    op.create_index("ix_product_assets_variant_id", "product_assets", ["variant_id"])

    op.create_table(
        "listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("platform", listing_platform, nullable=False),
        sa.Column("external_listing_id", sa.String(), nullable=True),
        sa.Column("ceiling_qty", sa.Integer(), nullable=True),
        sa.Column("last_synced_qty", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "external_listing_id", name="uq_listings_platform_external_id"),
    )

    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN NEW.updated_at = now(); RETURN NEW; END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_materials_updated_at BEFORE UPDATE ON materials
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON products
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_products_updated_at ON products")
    op.execute("DROP TRIGGER IF EXISTS trg_materials_updated_at ON materials")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    op.drop_table("listings")
    op.drop_table("product_assets")
    op.drop_table("product_variant_materials")
    op.drop_table("product_variants")
    op.drop_table("product_materials")
    op.drop_table("products")
    op.drop_table("material_purchases")
    op.drop_table("materials")

    bind = op.get_bind()
    listing_platform.drop(bind, checkfirst=True)
    asset_type.drop(bind, checkfirst=True)
    material_unit.drop(bind, checkfirst=True)
    material_category.drop(bind, checkfirst=True)
