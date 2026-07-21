"""bundle products

Revision ID: d53ac019fceb
Revises: 02ac4edc9f24
Create Date: 2026-07-08 09:34:04.013161

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd53ac019fceb'
down_revision: Union[str, Sequence[str], None] = '02ac4edc9f24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("is_bundle", sa.Boolean(), nullable=False, server_default="false"))

    op.create_table(
        "product_bundle_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bundle_product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "component_product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.UniqueConstraint("bundle_product_id", "component_product_id", name="uq_product_bundle_items_pair"),
        sa.CheckConstraint("qty > 0", name="ck_product_bundle_items_qty_positive"),
        sa.CheckConstraint(
            "bundle_product_id != component_product_id", name="ck_product_bundle_items_no_self_reference"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("product_bundle_items")
    op.drop_column("products", "is_bundle")
