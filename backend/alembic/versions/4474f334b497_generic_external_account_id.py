"""rename etsy_shop_id to external_account_id

Revision ID: 4474f334b497
Revises: f3b9d483892e
Create Date: 2026-07-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '4474f334b497'
down_revision: Union[str, Sequence[str], None] = 'f3b9d483892e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("platform_connections", "etsy_shop_id", new_column_name="external_account_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("platform_connections", "external_account_id", new_column_name="etsy_shop_id")
