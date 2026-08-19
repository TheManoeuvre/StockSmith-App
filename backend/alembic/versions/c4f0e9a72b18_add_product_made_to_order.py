"""add products.made_to_order

Some products are built against an order and never held on a shelf. Counting them means
walking to a place where, by design, there is nothing — so they were adding lines to every
stock take and sitting permanently on the due-for-counting list, which is how a list stops
being read.

Product-level rather than per variant. A made-to-order product's variants are made to
order too; a shop stocking some colourways and building others on demand has two products,
not one product with two kinds of variant.

Revision ID: c4f0e9a72b18
Revises: e7a4c2b91d35
Create Date: 2026-08-19 13:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4f0e9a72b18'
down_revision: Union[str, Sequence[str], None] = 'e7a4c2b91d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'products',
        sa.Column('made_to_order', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    with op.batch_alter_table('products') as batch:
        batch.drop_column('made_to_order')
