"""add blanks material category

Revision ID: c5d8f2a4e6b1
Revises: a1c3e9f7b2d4
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c5d8f2a4e6b1'
down_revision: Union[str, Sequence[str], None] = 'a1c3e9f7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE material_category ADD VALUE 'blanks'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres cannot drop a single value from an enum type, so this step is
    # irreversible short of recreating the type; no-op on downgrade.
    pass
