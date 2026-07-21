"""variant pricing modes

Revision ID: e6b83a5f9c14
Revises: d19e6f4a2c73
Create Date: 2026-07-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e6b83a5f9c14'
down_revision: Union[str, Sequence[str], None] = 'd19e6f4a2c73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

pricing_mode = postgresql.ENUM("product", "variable", "line", name="pricing_mode", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    pricing_mode.create(op.get_bind())

    op.add_column(
        "products",
        sa.Column("pricing_mode", pricing_mode, nullable=False, server_default="product"),
    )
    op.add_column("products", sa.Column("pricing_variable_attribute", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_products_pricing_variable_attribute_range",
        "products",
        "pricing_variable_attribute IS NULL OR pricing_variable_attribute BETWEEN 1 AND 3",
    )

    op.add_column("product_variants", sa.Column("sale_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("product_variants", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("product_variants", sa.Column("platform_fee_percent", sa.Numeric(5, 2), nullable=True))
    op.create_check_constraint(
        "ck_product_variants_sale_price_nonneg", "product_variants", "sale_price >= 0"
    )
    op.create_check_constraint(
        "ck_product_variants_shipping_cost_nonneg", "product_variants", "shipping_cost >= 0"
    )
    op.create_check_constraint(
        "ck_product_variants_platform_fee_percent_range",
        "product_variants",
        "platform_fee_percent >= 0 AND platform_fee_percent <= 100",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_product_variants_platform_fee_percent_range", "product_variants", type_="check")
    op.drop_constraint("ck_product_variants_shipping_cost_nonneg", "product_variants", type_="check")
    op.drop_constraint("ck_product_variants_sale_price_nonneg", "product_variants", type_="check")
    op.drop_column("product_variants", "platform_fee_percent")
    op.drop_column("product_variants", "shipping_cost")
    op.drop_column("product_variants", "sale_price")

    op.drop_constraint("ck_products_pricing_variable_attribute_range", "products", type_="check")
    op.drop_column("products", "pricing_variable_attribute")
    op.drop_column("products", "pricing_mode")

    bind = op.get_bind()
    pricing_mode.drop(bind, checkfirst=True)
