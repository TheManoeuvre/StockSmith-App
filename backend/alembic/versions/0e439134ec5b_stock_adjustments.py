"""stock adjustments

Revision ID: 0e439134ec5b
Revises: ab7b0c8a2ae7
Create Date: 2026-07-17 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0e439134ec5b'
down_revision: Union[str, Sequence[str], None] = 'ab7b0c8a2ae7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

stock_adjustment_mode = postgresql.ENUM("adjust", "set", name="stock_adjustment_mode", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    stock_adjustment_mode.create(op.get_bind())

    op.create_table(
        "stock_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("mode", stock_adjustment_mode, nullable=False, server_default="adjust"),
        sa.Column("qty_delta", sa.Integer(), nullable=False),
        sa.Column("target_qty", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_check_constraint(
        "ck_stock_adjustments_qty_delta_nonzero",
        "stock_adjustments",
        "qty_delta != 0 OR mode = 'set'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("stock_adjustments")

    bind = op.get_bind()
    stock_adjustment_mode.drop(bind, checkfirst=True)
