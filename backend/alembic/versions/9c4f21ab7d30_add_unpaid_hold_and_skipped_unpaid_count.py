"""add unpaid_hold_since and skipped_unpaid_count

Supports the paid-only order import gate: marketplace orders are no longer imported (and
therefore no longer reserve stock or push reduced quantities to live listings) until the
platform reports payment as settled.

Revision ID: 9c4f21ab7d30
Revises: 50380e3d75dc
Create Date: 2026-07-28 09:14:02.118374

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4f21ab7d30'
down_revision: Union[str, Sequence[str], None] = '50380e3d75dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # How far back to hold the order fetch window open so an order skipped for being
    # unpaid keeps being re-checked until it settles. Nullable and normally NULL —
    # existing rows correctly start with no hold, since nothing has been skipped yet.
    op.add_column(
        'platform_connections',
        sa.Column('unpaid_hold_since', sa.DateTime(timezone=True), nullable=True),
    )
    # Count of orders the gate held back on each sync run. server_default so existing
    # history backfills to 0 rather than NULL — those runs predate the gate, so 0 is
    # accurate, not merely convenient.
    op.add_column(
        'platform_sync_runs',
        sa.Column('skipped_unpaid_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('platform_sync_runs', 'skipped_unpaid_count')
    op.drop_column('platform_connections', 'unpaid_hold_since')
