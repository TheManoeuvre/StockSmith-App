"""add orders.manual_channel

A hand-entered order's own channel tag (Manual/Etsy/eBay, "keyed by hand"), distinct from
`orders.platform` — which means "this order was pulled in by marketplace sync" and gates
`_recompute_manual_order_totals` (orders.py) into a no-op because a synced order's totals
come from the receipt, not from recomputing line prices. Reusing `platform` for a manual
order's channel label would have set that same flag on manual orders and silently stopped
their totals from recomputing when lines change — this column exists so the two concerns
stay separate. NULL means "no channel picked" (renders as "Manual · direct sale").

Revision ID: 19e6521eb044
Revises: c7e2a9f3b104
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '19e6521eb044'
down_revision: Union[str, Sequence[str], None] = 'c7e2a9f3b104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MANUAL_ORDER_CHANNEL = sa.Enum('manual', 'etsy', 'ebay', name='manual_order_channel', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column('manual_channel', _MANUAL_ORDER_CHANNEL, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('manual_channel')
