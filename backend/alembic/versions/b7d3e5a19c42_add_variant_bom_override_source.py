"""add source to variant BOM overrides

Records whether a variant BOM override row was written by variant generation / a
bulk amend ("rule") or set by hand in the variant's BOM editor ("manual"), so bulk
amend can leave hand edits alone. Existing rows are "rule": their origin is unknown
and calling them manual would make bulk amend skip every one.

Revision ID: b7d3e5a19c42
Revises: 95c15157d55b
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d3e5a19c42'
down_revision: Union[str, Sequence[str], None] = '95c15157d55b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('product_variant_materials', 'product_variant_kitting_materials')


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('source', sa.String(length=6), server_default='rule', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    for table in reversed(_TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('source')
