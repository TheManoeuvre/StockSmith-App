"""merge the substitution-undo and shipping-price-refresh heads

Revision ID: b2c6e4d18f75
Revises: a9f3d7c21e58, f4b8d2c17a93
Create Date: 2026-09-16 13:30:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
# PRs #93 (a9f3d7c21e58) and #94/#95 (e3a9c7d51b04 -> f4b8d2c17a93) both branched from
# e7c2a95d1b48 and were merged independently, so 0.16.0 shipped with two heads and
# `alembic upgrade head` refused to run on every install. This empty merge revision joins
# them back into a single head; there is no schema change here.
revision: str = 'b2c6e4d18f75'
down_revision: Union[str, Sequence[str], None] = ('a9f3d7c21e58', 'f4b8d2c17a93')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
