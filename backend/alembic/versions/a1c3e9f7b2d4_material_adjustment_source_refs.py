"""material adjustment source refs (product/variant/order)

Revision ID: a1c3e9f7b2d4
Revises: 4474f334b497
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1c3e9f7b2d4'
down_revision: Union[str, Sequence[str], None] = '4474f334b497'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "material_adjustments",
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "material_adjustments",
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
        ),
    )
    op.add_column(
        "material_adjustments",
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("material_adjustments", "order_id")
    op.drop_column("material_adjustments", "variant_id")
    op.drop_column("material_adjustments", "product_id")
