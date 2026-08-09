"""add db_instance_id to general_settings

Identifies the database's lineage so connected clients can tell "the data changed" from "the
data was replaced". The value lives inside the database file, so a restored backup brings the
id of whatever database it was taken from — which is exactly the signal a thin client needs to
drop its whole query cache rather than merely refetch.

Nullable and unpopulated here on purpose: existing installs get an id lazily on the first read
of /system/status (see services/system_status.py). Backfilling in the migration would mean
generating a uuid in migration code, which is a data decision the application layer already
owns and would run again for every restored older database.

Revision ID: a1b7c3d92e50
Revises: d3f5a71c9e04
Create Date: 2026-08-09 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b7c3d92e50'
down_revision: Union[str, Sequence[str], None] = 'd3f5a71c9e04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('general_settings', sa.Column('db_instance_id', sa.String(length=36), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('general_settings', 'db_instance_id')
