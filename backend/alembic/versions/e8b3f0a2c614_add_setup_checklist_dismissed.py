"""add setup_checklist_dismissed to general_settings

The Dashboard's setup checklist (app/services/onboarding.py). It shows until every step is done or
the user dismisses it.

Existing installs that already hold data — exactly the ones the previous migration stamped with a
business type — start dismissed: their owner set the app up long before there was a checklist,
and a list of mostly-ticked steps would only be noise. The checklist stays one click away under
Help for anyone who wants it.

Revision ID: e8b3f0a2c614
Revises: d2e7a4c19f60
Create Date: 2026-11-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3f0a2c614'
down_revision: Union[str, Sequence[str], None] = 'd2e7a4c19f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('general_settings') as batch:
        batch.add_column(
            sa.Column('setup_checklist_dismissed', sa.Boolean(), server_default=sa.text('0'), nullable=False)
        )
    op.execute("UPDATE general_settings SET setup_checklist_dismissed = 1 WHERE business_type IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('general_settings') as batch:
        batch.drop_column('setup_checklist_dismissed')
