"""kitting boms

Revision ID: ae97d79165de
Revises: 0e439134ec5b
Create Date: 2026-07-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ae97d79165de'
down_revision: Union[str, Sequence[str], None] = '0e439134ec5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("materials", sa.Column("allocated_qty", sa.Numeric(14, 4), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_materials_allocated_qty_range", "materials", "allocated_qty >= 0 AND allocated_qty <= current_qty"
    )

    op.create_table(
        "product_kitting_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty_required", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("product_id", "material_id", name="uq_product_kitting_materials_product_material"),
        sa.CheckConstraint("qty_required > 0", name="ck_product_kitting_materials_qty_required_positive"),
    )
    op.create_index("ix_product_kitting_materials_product_id", "product_kitting_materials", ["product_id"])

    op.create_table(
        "product_variant_kitting_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty_required", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "replaces_material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.UniqueConstraint(
            "variant_id", "material_id", name="uq_product_variant_kitting_materials_variant_material"
        ),
        sa.CheckConstraint("qty_required >= 0", name="ck_product_variant_kitting_materials_qty_required_nonneg"),
        sa.CheckConstraint(
            "replaces_material_id IS NULL OR replaces_material_id != material_id",
            name="ck_product_variant_kitting_materials_no_self_substitution",
        ),
    )
    op.create_index(
        "ix_product_variant_kitting_materials_variant_id", "product_variant_kitting_materials", ["variant_id"]
    )

    op.create_table(
        "order_kitting_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty_required", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "replaces_material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.UniqueConstraint("order_id", "material_id", name="uq_order_kitting_overrides_order_material"),
        sa.CheckConstraint("qty_required >= 0", name="ck_order_kitting_overrides_qty_required_nonneg"),
        sa.CheckConstraint(
            "replaces_material_id IS NULL OR replaces_material_id != material_id",
            name="ck_order_kitting_overrides_no_self_substitution",
        ),
    )
    op.create_index("ix_order_kitting_overrides_order_id", "order_kitting_overrides", ["order_id"])

    op.create_table(
        "order_kitting_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reserved_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("consumed_qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "material_id", name="uq_order_kitting_allocations_order_material"),
        sa.CheckConstraint("reserved_qty >= 0", name="ck_order_kitting_allocations_reserved_qty_nonneg"),
        sa.CheckConstraint("consumed_qty >= 0", name="ck_order_kitting_allocations_consumed_qty_nonneg"),
    )
    op.create_index("ix_order_kitting_allocations_order_id", "order_kitting_allocations", ["order_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("order_kitting_allocations")
    op.drop_table("order_kitting_overrides")
    op.drop_table("product_variant_kitting_materials")
    op.drop_table("product_kitting_materials")

    op.drop_constraint("ck_materials_allocated_qty_range", "materials", type_="check")
    op.drop_column("materials", "allocated_qty")
