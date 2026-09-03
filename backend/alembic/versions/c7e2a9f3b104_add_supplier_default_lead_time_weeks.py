"""add suppliers.default_lead_time_weeks

A per-supplier lead time (weeks) so the materials Weeks-of-Supply forecast can judge a
reorder point against how long *that* supplier actually takes, rather than only the
shop-wide GeneralSettings.default_lead_time_weeks. NULL keeps the shop-wide fallback, so
existing suppliers behave exactly as before until someone fills the field in.

Plain nullable column, no backfill — NULL is a meaningful value here ("no supplier-specific
figure"), not a gap to fill.

Revision ID: c7e2a9f3b104
Revises: b4d9f1e07a26
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a9f3b104'
down_revision: Union[str, Sequence[str], None] = 'b4d9f1e07a26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'suppliers',
        sa.Column('default_lead_time_weeks', sa.Numeric(6, 2), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('suppliers') as batch:
        batch.drop_column('default_lead_time_weeks')
