"""etsy sync start date

Revision ID: c4d76f2b8a91
Revises: a8c3f1d9e246
Create Date: 2026-07-16 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4d76f2b8a91'
down_revision: Union[str, Sequence[str], None] = 'a8c3f1d9e246'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("platform_connections", sa.Column("sync_start_date", sa.Date(), nullable=True))
    # Backfill existing connections (created before this column existed) with the same
    # 14-day-back default new connections get, so a shop connected pre-migration doesn't
    # end up with no floor at all on its next sync.
    op.execute("UPDATE platform_connections SET sync_start_date = CURRENT_DATE - INTERVAL '14 days' WHERE sync_start_date IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("platform_connections", "sync_start_date")
