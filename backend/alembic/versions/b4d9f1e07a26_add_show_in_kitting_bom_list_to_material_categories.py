"""add show_in_kitting_bom_list to material_categories

The kitting-BOM material pickers (product kitting BOM, variant kitting overrides, order
kitting overrides) list every material in the catalogue, grouped by category. In practice
only packaging ever gets picked there, so the user scrolls past filament, resin and hardware
every time. This adds a per-category opt-in flag so those pickers can show only the
categories that are actually kitting materials.

Purely a UI filter — nothing stops an already-saved kitting line pointing at a material
whose category is unticked, and no other behaviour keys on it. Seeded on for `packaging`
alone, matching where auto_kitting_per_order already sits; every other category (including
user-created ones, via the column default) starts off.

Revision ID: b4d9f1e07a26
Revises: e2b5a1c9d4f7
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d9f1e07a26'
down_revision: Union[str, Sequence[str], None] = 'e2b5a1c9d4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'material_categories',
        sa.Column(
            'show_in_kitting_bom_list',
            sa.Boolean(),
            server_default=sa.text('0'),
            nullable=False,
        ),
    )
    # Seed the one category the app used to hardcode as "kitting". Matched by name — the seven
    # original categories are the only rows that can exist at this revision, and 'packaging' is
    # among them.
    op.get_bind().execute(
        sa.text(
            "UPDATE material_categories SET show_in_kitting_bom_list = 1 WHERE name = 'packaging'"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('material_categories') as batch:
        batch.drop_column('show_in_kitting_bom_list')
