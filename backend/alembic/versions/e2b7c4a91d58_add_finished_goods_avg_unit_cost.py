"""add products/product_variants.avg_unit_cost

Running weighted-average build cost of finished stock, so a build that draws a short line
from a fallback material is costed at what was actually consumed rather than at the BOM as
written. See Product.avg_unit_cost.

Plain nullable columns, no backfill — NULL is meaningful ("never blended; resolve COGS from
the BOM"), and existing stock keeps costing exactly as it did.

Revision ID: e2b7c4a91d58
Revises: a7c2e4b19d35
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2b7c4a91d58'
down_revision: Union[str, Sequence[str], None] = 'a7c2e4b19d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('products', sa.Column('avg_unit_cost', sa.Numeric(14, 6), nullable=True))
    op.add_column('product_variants', sa.Column('avg_unit_cost', sa.Numeric(14, 6), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('product_variants') as batch:
        batch.drop_column('avg_unit_cost')
    with op.batch_alter_table('products') as batch:
        batch.drop_column('avg_unit_cost')
