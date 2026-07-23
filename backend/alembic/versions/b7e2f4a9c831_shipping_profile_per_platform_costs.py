"""shipping profile per-platform costs

Revision ID: b7e2f4a9c831
Revises: a4c8e2f6b193
Create Date: 2026-07-23 14:00:00.000000

Replaces ShippingProfile.cost with cost_etsy/cost_ebay/cost_manual — the same
physical shipping method can genuinely cost different amounts depending on where the
label is bought (Etsy's own label-purchase price vs. eBay's vs. paying a carrier
directly for a manual order). Existing cost values are copied into all three columns
as a starting point — the user should review and split them per channel afterward.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7e2f4a9c831'
down_revision: Union[str, Sequence[str], None] = 'a4c8e2f6b193'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("shipping_profiles", sa.Column("cost_etsy", sa.Numeric(10, 2), nullable=True))
    op.add_column("shipping_profiles", sa.Column("cost_ebay", sa.Numeric(10, 2), nullable=True))
    op.add_column("shipping_profiles", sa.Column("cost_manual", sa.Numeric(10, 2), nullable=True))

    op.execute("UPDATE shipping_profiles SET cost_etsy = cost, cost_ebay = cost, cost_manual = cost")

    op.alter_column("shipping_profiles", "cost_etsy", nullable=False, server_default="0")
    op.alter_column("shipping_profiles", "cost_ebay", nullable=False, server_default="0")
    op.alter_column("shipping_profiles", "cost_manual", nullable=False, server_default="0")

    op.create_check_constraint("ck_shipping_profiles_cost_etsy_nonneg", "shipping_profiles", "cost_etsy >= 0")
    op.create_check_constraint("ck_shipping_profiles_cost_ebay_nonneg", "shipping_profiles", "cost_ebay >= 0")
    op.create_check_constraint("ck_shipping_profiles_cost_manual_nonneg", "shipping_profiles", "cost_manual >= 0")

    op.drop_constraint("ck_shipping_profiles_cost_nonneg", "shipping_profiles", type_="check")
    op.drop_column("shipping_profiles", "cost")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("shipping_profiles", sa.Column("cost", sa.Numeric(10, 2), nullable=True))
    op.execute("UPDATE shipping_profiles SET cost = cost_etsy")
    op.alter_column("shipping_profiles", "cost", nullable=False, server_default="0")
    op.create_check_constraint("ck_shipping_profiles_cost_nonneg", "shipping_profiles", "cost >= 0")

    op.drop_constraint("ck_shipping_profiles_cost_manual_nonneg", "shipping_profiles", type_="check")
    op.drop_constraint("ck_shipping_profiles_cost_ebay_nonneg", "shipping_profiles", type_="check")
    op.drop_constraint("ck_shipping_profiles_cost_etsy_nonneg", "shipping_profiles", type_="check")

    op.drop_column("shipping_profiles", "cost_manual")
    op.drop_column("shipping_profiles", "cost_ebay")
    op.drop_column("shipping_profiles", "cost_etsy")
