"""product barcode

Revision ID: 464ccf61382a
Revises: e5d0340a9395
Create Date: 2026-07-08 08:50:13.807373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '464ccf61382a'
down_revision: Union[str, Sequence[str], None] = 'e5d0340a9395'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("barcode", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("products", "barcode")
