"""order sync issue flag

Revision ID: f3b9d483892e
Revises: c9f7aeb2792a
Create Date: 2026-07-17 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3b9d483892e'
down_revision: Union[str, Sequence[str], None] = 'c9f7aeb2792a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("sync_issue", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "sync_issue")
