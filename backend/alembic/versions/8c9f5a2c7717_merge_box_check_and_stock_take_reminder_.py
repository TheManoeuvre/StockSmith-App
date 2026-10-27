"""merge box check and stock take reminder heads

Revision ID: 8c9f5a2c7717
Revises: d4e8b2a6c917, f3c8a1d5e792
Create Date: 2026-10-27 00:08:45.582530

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c9f5a2c7717'
down_revision: Union[str, Sequence[str], None] = ('d4e8b2a6c917', 'f3c8a1d5e792')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
