"""finished goods stock and builds

Revision ID: 02ac4edc9f24
Revises: ef7aeb026c43
Create Date: 2026-07-08 09:22:57.139822

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02ac4edc9f24'
down_revision: Union[str, Sequence[str], None] = 'ef7aeb026c43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("current_stock", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_products_current_stock_nonneg", "products", "current_stock >= 0")

    op.add_column("product_variants", sa.Column("current_stock", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_product_variants_current_stock_nonneg", "product_variants", "current_stock >= 0"
    )

    op.create_table(
        "builds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("qty_built", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty_built > 0", name="ck_builds_qty_built_positive"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("builds")

    op.drop_constraint("ck_product_variants_current_stock_nonneg", "product_variants", type_="check")
    op.drop_column("product_variants", "current_stock")

    op.drop_constraint("ck_products_current_stock_nonneg", "products", type_="check")
    op.drop_column("products", "current_stock")
