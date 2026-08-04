"""index sync run and listing push lookups

Both indexes support services/sync_status.py, which the menu-bar sync indicator polls
every 60s. Before this, "latest commit run for this platform" and "latest push attempt per
listing" were table scans that only ran on the occasional settings page load — cheap when
rare, not something to repeat on a timer as those tables grow (both are append-only).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_platform_sync_runs_platform_started_at',
        'platform_sync_runs',
        ['platform', 'started_at'],
        unique=False,
    )
    op.create_index(
        'ix_platform_listing_pushes_target_attempted_at',
        'platform_listing_pushes',
        ['platform', 'product_id', 'variant_id', 'attempted_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_platform_listing_pushes_target_attempted_at', table_name='platform_listing_pushes')
    op.drop_index('ix_platform_sync_runs_platform_started_at', table_name='platform_sync_runs')
