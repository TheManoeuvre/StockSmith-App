"""variant attribute matrix

Revision ID: 812a657d8f98
Revises: 41e0fc2026ba
Create Date: 2026-07-08 09:52:47.347223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '812a657d8f98'
down_revision: Union[str, Sequence[str], None] = '41e0fc2026ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("variant_attribute1_name", sa.String(), nullable=True))
    op.add_column("products", sa.Column("variant_attribute2_name", sa.String(), nullable=True))
    op.add_column("products", sa.Column("variant_attribute3_name", sa.String(), nullable=True))

    op.add_column("product_variants", sa.Column("attribute1_value", sa.String(), nullable=True))
    op.add_column("product_variants", sa.Column("attribute2_value", sa.String(), nullable=True))
    op.add_column("product_variants", sa.Column("attribute3_value", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_product_variants_attribute_combo",
        "product_variants",
        ["product_id", "attribute1_value", "attribute2_value", "attribute3_value"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_product_variants_attribute_combo", "product_variants", type_="unique")
    op.drop_column("product_variants", "attribute3_value")
    op.drop_column("product_variants", "attribute2_value")
    op.drop_column("product_variants", "attribute1_value")

    op.drop_column("products", "variant_attribute3_name")
    op.drop_column("products", "variant_attribute2_name")
    op.drop_column("products", "variant_attribute1_name")
