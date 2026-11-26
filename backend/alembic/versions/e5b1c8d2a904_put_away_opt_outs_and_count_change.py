"""Put-away opt-outs, and what a by-location count line did to stock

Revision ID: e5b1c8d2a904
Revises: d3a9b5e7c102
Create Date: 2026-11-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b1c8d2a904"
down_revision: Union[str, Sequence[str], None] = "d3a9b5e7c102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stock_take_lines", sa.Column("stock_change", sa.Numeric(14, 4), nullable=True))
    op.create_table(
        "put_away_opt_outs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
        ),
    )
    op.create_index("ix_put_away_opt_outs_material_id", "put_away_opt_outs", ["material_id"])
    op.create_index("ix_put_away_opt_outs_product_id", "put_away_opt_outs", ["product_id"])


def downgrade() -> None:
    op.drop_table("put_away_opt_outs")
    with op.batch_alter_table("stock_take_lines") as batch:
        batch.drop_column("stock_change")
