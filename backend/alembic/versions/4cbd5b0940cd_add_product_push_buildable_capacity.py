"""add product push_buildable_capacity

Revision ID: 4cbd5b0940cd
Revises: dad84c4b7c9a
Create Date: 2026-07-24 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4cbd5b0940cd'
down_revision: Union[str, Sequence[str], None] = 'dad84c4b7c9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column('push_buildable_capacity', sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('products', 'push_buildable_capacity')
