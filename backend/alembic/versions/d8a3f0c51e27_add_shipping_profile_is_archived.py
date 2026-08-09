"""add is_archived to shipping_profiles

Orders reference a shipping profile, so deleting one silently rewrites what a historical order
was shipped under, and merging would do the same. Archiving retires a profile from the pickers
while leaving every existing reference intact — which is what "we don't offer this any more"
actually means.

Revision ID: d8a3f0c51e27
Revises: c4e8f21a7b93
Create Date: 2026-08-09 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8a3f0c51e27'
down_revision: Union[str, Sequence[str], None] = 'c4e8f21a7b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'shipping_profiles',
        sa.Column('is_archived', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('shipping_profiles', 'is_archived')
