"""add business_type to general_settings

The first-launch "what do you make?" answer (see app/services/business_presets.py). NULL means a
fresh install that hasn't been asked yet, and is what the frontend shows the picker on.

Existing installs must never be asked: the picker replaces the material categories, and they
already have their own. So any database that already holds materials, products or orders is
stamped "3d_printing" — the preset that is exactly the seven categories they were seeded with.

On a brand-new install this UPDATE touches nothing: the general_settings row is inserted by
app/seed.py *after* migrations run, so it is created with business_type NULL. A database that
exists but holds no stock or orders is left NULL too and gets the picker, which is safe — with no
materials, nothing references the categories it replaces.

Revision ID: d2e7a4c19f60
Revises: c7d3a915e284
Create Date: 2026-11-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e7a4c19f60'
down_revision: Union[str, Sequence[str], None] = 'c7d3a915e284'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('general_settings') as batch:
        batch.add_column(sa.Column('business_type', sa.String(length=32), nullable=True))

    op.execute(
        """
        UPDATE general_settings
        SET business_type = '3d_printing'
        WHERE EXISTS (SELECT 1 FROM materials)
           OR EXISTS (SELECT 1 FROM products)
           OR EXISTS (SELECT 1 FROM orders)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('general_settings') as batch:
        batch.drop_column('business_type')
