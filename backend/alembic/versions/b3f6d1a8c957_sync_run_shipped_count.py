"""sync run shipped count

Revision ID: b3f6d1a8c957
Revises: f7a2c8e1b4d9
Create Date: 2026-07-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b3f6d1a8c957'
down_revision: Union[str, Sequence[str], None] = 'f7a2c8e1b4d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "platform_sync_runs", sa.Column("shipped_count", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("platform_sync_runs", "shipped_count")
