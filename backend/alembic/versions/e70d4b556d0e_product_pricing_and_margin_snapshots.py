"""product pricing and margin snapshots

Revision ID: e70d4b556d0e
Revises: 812a657d8f98
Create Date: 2026-07-08 10:01:33.515834

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e70d4b556d0e'
down_revision: Union[str, Sequence[str], None] = '812a657d8f98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("sale_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("products", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("products", sa.Column("platform_fee_percent", sa.Numeric(5, 2), nullable=True))
    op.create_check_constraint("ck_products_sale_price_nonneg", "products", "sale_price >= 0")
    op.create_check_constraint("ck_products_shipping_cost_nonneg", "products", "shipping_cost >= 0")
    op.create_check_constraint(
        "ck_products_platform_fee_percent_range",
        "products",
        "platform_fee_percent >= 0 AND platform_fee_percent <= 100",
    )

    op.create_table(
        "product_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cost_per_unit", sa.Numeric(14, 6), nullable=False),
        sa.Column("sale_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("margin_percent", sa.Numeric(6, 2), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("product_price_snapshots")

    op.drop_constraint("ck_products_platform_fee_percent_range", "products", type_="check")
    op.drop_constraint("ck_products_shipping_cost_nonneg", "products", type_="check")
    op.drop_constraint("ck_products_sale_price_nonneg", "products", type_="check")
    op.drop_column("products", "platform_fee_percent")
    op.drop_column("products", "shipping_cost")
    op.drop_column("products", "sale_price")
