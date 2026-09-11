"""add notifications

Backend for the Notifications feature: per-install channel/quiet-hours/summary
configuration (notification_settings, single row), per-alert-type enable/delivery-mode
(notification_type_settings, one row per type), the in-app notification log
(notifications), and dedup memory for alert conditions that must fire only on a
transition rather than every periodic poll (notification_alert_state).

Seed rows (the settings singleton and the 9 notification_type_settings rows) are inserted
by app/seed.py, not here — same convention as general_settings/backup_settings: seed data
doesn't belong in a schema migration that might later be squashed.

Revision ID: fdc3ea941e39
Revises: c9d1f4a7b230
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'fdc3ea941e39'
down_revision: Union[str, Sequence[str], None] = 'c9d1f4a7b230'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_CATEGORY = sa.Enum(
    'marketplace_sync_failure',
    'material_forecast_critical',
    'material_forecast_warning',
    'order_unfulfillable',
    'pending_order_threshold',
    'backup_failed',
    'secondary_backup_unreachable',
    'marketplace_api_soft_limit',
    'marketplace_api_hard_limit',
    'daily_summary',
    name='notification_category',
    native_enum=False,
)
_NOTIFICATION_URGENCY = sa.Enum('immediate', 'digest', name='notification_urgency', native_enum=False)
_NOTIFICATION_DELIVERY_MODE = sa.Enum(
    'immediate', 'digest', 'off', name='notification_delivery_mode', native_enum=False
)
_SUMMARY_FREQUENCY = sa.Enum('daily', 'weekly', name='notification_summary_frequency', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'notification_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('windows_notifications_enabled', sa.Boolean(), nullable=False),
        sa.Column('pushover_enabled', sa.Boolean(), nullable=False),
        sa.Column('pushover_user_key', sa.String(), nullable=True),
        sa.Column('quiet_hours_enabled', sa.Boolean(), nullable=False),
        sa.Column('quiet_hours_start', sa.Integer(), nullable=False),
        sa.Column('quiet_hours_end', sa.Integer(), nullable=False),
        sa.Column('daily_summary_enabled', sa.Boolean(), nullable=False),
        sa.Column('daily_summary_frequency', _SUMMARY_FREQUENCY, nullable=False),
        sa.Column('daily_summary_hour_local', sa.Integer(), nullable=False),
        sa.Column('daily_summary_day_of_week', sa.Integer(), nullable=True),
        sa.Column('daily_summary_last_fired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pending_order_threshold', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('quiet_hours_start >= 0 AND quiet_hours_start <= 23', name='ck_notif_quiet_start_range'),
        sa.CheckConstraint('quiet_hours_end >= 0 AND quiet_hours_end <= 23', name='ck_notif_quiet_end_range'),
        sa.CheckConstraint(
            'daily_summary_hour_local >= 0 AND daily_summary_hour_local <= 23', name='ck_notif_summary_hour_range'
        ),
        sa.CheckConstraint(
            'daily_summary_day_of_week IS NULL OR (daily_summary_day_of_week >= 0 AND daily_summary_day_of_week <= 6)',
            name='ck_notif_summary_dow_range',
        ),
        sa.CheckConstraint('pending_order_threshold >= 1', name='ck_notif_pending_order_threshold_positive'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'notification_type_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alert_type', _NOTIFICATION_CATEGORY, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('delivery_mode', _NOTIFICATION_DELIVERY_MODE, nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alert_type'),
    )

    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category', _NOTIFICATION_CATEGORY, nullable=False),
        sa.Column('urgency', _NOTIFICATION_URGENCY, nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('body', sa.String(), nullable=False),
        sa.Column('related_entity_type', sa.String(), nullable=True),
        sa.Column('related_entity_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('digest_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notifications_created_at', 'notifications', ['created_at'])
    op.create_index('ix_notifications_read_at', 'notifications', ['read_at'])

    op.create_table(
        'notification_alert_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('alert_key', sa.String(), nullable=False),
        sa.Column('entity_key', sa.String(), nullable=False),
        sa.Column('last_status', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('alert_key', 'entity_key', name='uq_notification_alert_state_key_entity'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('notification_alert_state')
    op.drop_index('ix_notifications_read_at', table_name='notifications')
    op.drop_index('ix_notifications_created_at', table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('notification_type_settings')
    op.drop_table('notification_settings')
