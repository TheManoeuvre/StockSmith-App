"""add purchases.delivery_cost

Delivery / carriage charged on a purchase order as a whole. Nullable, no backfill: NULL
means "no delivery charge recorded", which the UI shows differently from an entered 0.00.
Shown on the order total only — services/costing.py does not apportion it to unit costs.

Revision ID: b4d2f8c1a6e9
Revises: a3f1c9e07b24
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d2f8c1a6e9'
down_revision: Union[str, Sequence[str], None] = 'a3f1c9e07b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'purchases',
        sa.Column('delivery_cost', sa.Numeric(14, 2), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('purchases') as batch:
        batch.drop_column('delivery_cost')
