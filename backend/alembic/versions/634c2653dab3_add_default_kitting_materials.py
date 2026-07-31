"""add default_kitting_materials

Backs the user-configured "Default kitting BOM" setting (Settings > General), which
replaces the auto-created "4x6 Direct Thermal Label" material from the previous release —
that logic get-or-created a Material by exact name match, and on any environment where the
name didn't match an existing material exactly, silently created a brand-new, never-
stocked material and attached it to every new product. See
scripts/cleanup_ghost_default_label.py for cleaning up any ghost material/lines that
release already created.

Revision ID: 634c2653dab3
Revises: 4a7c18daa1eb
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '634c2653dab3'
down_revision: Union[str, Sequence[str], None] = '4a7c18daa1eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'default_kitting_materials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('material_id', sa.Integer(), nullable=False),
        sa.Column('qty_required', sa.Numeric(14, 4), nullable=False),
        sa.CheckConstraint('qty_required > 0', name='ck_default_kitting_materials_qty_required_positive'),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('material_id', name='uq_default_kitting_materials_material'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('default_kitting_materials')
