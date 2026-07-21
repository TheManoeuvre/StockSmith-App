"""listing external variation

Revision ID: d19e6f4a2c73
Revises: c4d76f2b8a91
Create Date: 2026-07-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd19e6f4a2c73'
down_revision: Union[str, Sequence[str], None] = 'c4d76f2b8a91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("listings", sa.Column("external_variation", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("listings", "external_variation")
