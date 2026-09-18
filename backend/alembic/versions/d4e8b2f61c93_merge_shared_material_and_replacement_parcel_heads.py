"""merge the shared-material-variant and replacement-parcel heads

Revision ID: d4e8b2f61c93
Revises: c3d7a9e14f52, c7d3a9e51b04
Create Date: 2026-09-18 13:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
# PRs #120 (c7d3a9e51b04) and #122 (c3d7a9e14f52) both branched from b2c6e4d18f75 and
# were merged independently, leaving main with two heads — the same shape that broke
# 0.16.0's startup. Caught by test_migration_heads before the 0.17.0 tag this time. This
# empty merge revision joins them back into a single head; there is no schema change here.
revision: str = 'd4e8b2f61c93'
down_revision: Union[str, Sequence[str], None] = ('c3d7a9e14f52', 'c7d3a9e51b04')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
