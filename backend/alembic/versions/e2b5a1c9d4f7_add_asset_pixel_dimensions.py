"""add pixel dimensions to product assets

The reviewed design's Assets tab shows each file's dimensions ("1600x1600"). Nothing
recorded them, so add two plain nullable columns, populated at upload/import time for
image asset types (CAD/gcode stay null). Existing image rows are filled by
scripts/backfill_asset_dimensions.py, which reads the files off disk.

Revision ID: e2b5a1c9d4f7
Revises: d1a4f8c62b95
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2b5a1c9d4f7'
down_revision: Union[str, Sequence[str], None] = 'd1a4f8c62b95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('product_assets', sa.Column('width_px', sa.Integer(), nullable=True))
    op.add_column('product_assets', sa.Column('height_px', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('product_assets') as batch:
        batch.drop_column('height_px')
        batch.drop_column('width_px')
