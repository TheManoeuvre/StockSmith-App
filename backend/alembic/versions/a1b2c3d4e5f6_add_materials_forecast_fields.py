"""add materials forecast fields

Revision ID: a1b2c3d4e5f6
Revises: 634c2653dab3
Create Date: 2026-07-31 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '634c2653dab3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Materials Weeks-of-Supply dashboard thresholds/lookback, plus a shop-wide default
    # lead time used to estimate arrival for on-order purchases with no explicit ETA.
    # server_default so the existing single settings row backfills to the same values
    # new rows would get from the model's Python-side defaults.
    op.add_column(
        'general_settings',
        sa.Column('forecast_warning_weeks', sa.Numeric(6, 2), server_default='6', nullable=False),
    )
    op.add_column(
        'general_settings',
        sa.Column('forecast_critical_weeks', sa.Numeric(6, 2), server_default='2', nullable=False),
    )
    op.add_column(
        'general_settings',
        sa.Column('forecast_lookback_weeks', sa.Integer(), server_default='8', nullable=False),
    )
    op.add_column(
        'general_settings',
        sa.Column('default_lead_time_weeks', sa.Numeric(6, 2), server_default='4', nullable=False),
    )
    # Optional PO-level ETA — NULL for existing/legacy purchases, which the forecast
    # instead estimates from order_date + default_lead_time_weeks.
    op.add_column('purchases', sa.Column('expected_arrival_date', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('purchases', 'expected_arrival_date')
    op.drop_column('general_settings', 'default_lead_time_weeks')
    op.drop_column('general_settings', 'forecast_lookback_weeks')
    op.drop_column('general_settings', 'forecast_critical_weeks')
    op.drop_column('general_settings', 'forecast_warning_weeks')
