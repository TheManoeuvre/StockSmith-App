"""platform shop identity

Revision ID: b8e4f1a7c3d2
Revises: c5d8f2a4e6b1
Create Date: 2026-07-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8e4f1a7c3d2'
down_revision: Union[str, Sequence[str], None] = 'c5d8f2a4e6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("platform_connections", sa.Column("shop_name", sa.String(), nullable=True))
    op.add_column("platform_connections", sa.Column("shop_icon_path", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("platform_connections", "shop_icon_path")
    op.drop_column("platform_connections", "shop_name")
