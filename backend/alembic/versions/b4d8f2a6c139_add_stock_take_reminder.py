"""add stock take reminder settings

Schedule columns for the "items due for counting" notification, mirroring the order summary's
daily_summary_* columns on notification_settings. Off by default; weekly on Monday at 09:00
once switched on. The new `stock_take_due` notification category needs no column change: the
category column is a VARCHAR already wide enough for it.

Revision ID: b4d8f2a6c139
Revises: a8d4e2f61c93
Create Date: 2026-10-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d8f2a6c139'
down_revision: Union[str, Sequence[str], None] = 'a8d4e2f61c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SUMMARY_FREQUENCY = sa.Enum('daily', 'weekly', name='notification_summary_frequency', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'notification_settings',
        sa.Column('stock_take_reminder_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'notification_settings',
        sa.Column('stock_take_reminder_frequency', _SUMMARY_FREQUENCY, nullable=False, server_default='weekly'),
    )
    op.add_column(
        'notification_settings',
        sa.Column('stock_take_reminder_hour_local', sa.Integer(), nullable=False, server_default='9'),
    )
    op.add_column(
        'notification_settings',
        sa.Column('stock_take_reminder_day_of_week', sa.Integer(), nullable=True, server_default='0'),
    )
    op.add_column(
        'notification_settings',
        sa.Column('stock_take_reminder_last_fired_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notification_settings') as batch:
        batch.drop_column('stock_take_reminder_last_fired_at')
        batch.drop_column('stock_take_reminder_day_of_week')
        batch.drop_column('stock_take_reminder_hour_local')
        batch.drop_column('stock_take_reminder_frequency')
        batch.drop_column('stock_take_reminder_enabled')
