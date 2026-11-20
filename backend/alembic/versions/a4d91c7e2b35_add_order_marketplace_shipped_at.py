"""add marketplace_shipped_at to orders

When a sync first saw the marketplace report the order shipped. An order the marketplace shipped
but StockSmith still holds open (box check, nothing allocated) is otherwise invisible once the
sync's flag is cleared — the sync never fetches an unchanged order again.

Backfill: a synced order only carries a tracking number once the marketplace has shipped it
(order_sync._apply_financials), so every still-open synced order with one is a mismatch today.
Its updated_at is the best stand-in for when. Shipped orders are left blank: nothing to flag.

Revision ID: a4d91c7e2b35
Revises: e8b3f0a2c614
Create Date: 2026-11-19 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d91c7e2b35'
down_revision: Union[str, Sequence[str], None] = 'e8b3f0a2c614'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('orders') as batch:
        batch.add_column(sa.Column('marketplace_shipped_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE orders SET marketplace_shipped_at = updated_at "
        "WHERE platform IS NOT NULL AND tracking_number IS NOT NULL "
        "AND status IN ('pending', 'allocated')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('marketplace_shipped_at')
