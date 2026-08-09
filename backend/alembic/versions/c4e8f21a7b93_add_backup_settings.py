"""add backup_settings

Single-row configuration for scheduled database backups. Deliberately holds no record of the
backups themselves — the archives on disk are the source of truth, so listing them means
reading the directory rather than a table that drifts whenever someone deletes a file in
Explorer.

The row itself is inserted by app/seed.py rather than here, matching general_settings and
margin_fee_config: seed data doesn't belong in a schema migration that might later be squashed.

Revision ID: c4e8f21a7b93
Revises: a1b7c3d92e50
Create Date: 2026-08-09 11:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8f21a7b93'
down_revision: Union[str, Sequence[str], None] = 'a1b7c3d92e50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'backup_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scheduled_enabled', sa.Boolean(), nullable=False),
        sa.Column('scheduled_hour_local', sa.Integer(), nullable=False),
        sa.Column('retention_count', sa.Integer(), nullable=False),
        sa.Column('secondary_dir', sa.String(), nullable=True),
        sa.Column('secondary_dir_last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('secondary_dir_last_error', sa.String(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(), nullable=True),
        sa.Column('last_run_error', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('scheduled_hour_local >= 0 AND scheduled_hour_local <= 23', name='ck_backup_hour_range'),
        sa.CheckConstraint('retention_count >= 1', name='ck_backup_retention_positive'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('backup_settings')
