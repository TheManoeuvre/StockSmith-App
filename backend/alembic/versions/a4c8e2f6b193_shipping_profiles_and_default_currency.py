"""shipping profiles and default currency

Revision ID: a4c8e2f6b193
Revises: e2b6c0a4d8f1
Create Date: 2026-07-23 09:00:00.000000

Replaces the old flat Product/ProductVariant.shipping_cost number (which was used both
as a cost and, inconsistently, as a stand-in for what's charged to the customer) with
named, reusable ShippingProfile rows carrying both a price (charged) and a cost (to the
seller). Existing shipping_cost values can't be meaningfully turned into named profiles
(no name, no charged-price counterpart) so they're simply dropped — products will show
"no shipping profile" until the user creates profiles and assigns them.

Also adds a single-row general_settings table for a shop-wide default currency, used only
to pre-fill new manual orders (no FX conversion anywhere).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4c8e2f6b193'
down_revision: Union[str, Sequence[str], None] = 'e2b6c0a4d8f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

currency_code = postgresql.ENUM("GBP", "USD", "EUR", name="currency_code", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    currency_code.create(op.get_bind())

    op.create_table(
        "shipping_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("price >= 0", name="ck_shipping_profiles_price_nonneg"),
        sa.CheckConstraint("cost >= 0", name="ck_shipping_profiles_cost_nonneg"),
    )

    op.create_table(
        "general_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("default_currency", currency_code, nullable=False, server_default="GBP"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("INSERT INTO general_settings (id, default_currency) VALUES (1, 'GBP')")

    op.drop_constraint("ck_products_shipping_cost_nonneg", "products", type_="check")
    op.drop_column("products", "shipping_cost")
    op.add_column(
        "products", sa.Column("shipping_profile_id", sa.Integer(), sa.ForeignKey("shipping_profiles.id", ondelete="SET NULL"), nullable=True)
    )

    op.drop_constraint("ck_product_variants_shipping_cost_nonneg", "product_variants", type_="check")
    op.drop_column("product_variants", "shipping_cost")
    op.add_column(
        "product_variants",
        sa.Column("shipping_profile_id", sa.Integer(), sa.ForeignKey("shipping_profiles.id", ondelete="SET NULL"), nullable=True),
    )

    op.add_column(
        "orders",
        sa.Column("shipping_profile_id", sa.Integer(), sa.ForeignKey("shipping_profiles.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("orders", sa.Column("shipping_cost_snapshot", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "shipping_cost_snapshot")
    op.drop_column("orders", "shipping_profile_id")

    op.drop_column("product_variants", "shipping_profile_id")
    op.add_column("product_variants", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.create_check_constraint("ck_product_variants_shipping_cost_nonneg", "product_variants", "shipping_cost >= 0")

    op.drop_column("products", "shipping_profile_id")
    op.add_column("products", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.create_check_constraint("ck_products_shipping_cost_nonneg", "products", "shipping_cost >= 0")

    op.drop_table("general_settings")
    op.drop_table("shipping_profiles")

    bind = op.get_bind()
    currency_code.drop(bind, checkfirst=True)
