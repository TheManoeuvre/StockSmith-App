"""add material substitutes and usage log

Material-level substitution fallbacks: material_substitutes records that
substitute_material_id is a human-curated stand-in for material_id, so a fallback
declared once applies at every product/BOM/kitting line referencing that material —
unlike ProductVariantMaterial.replaces_material_id (and its
ProductVariantKittingMaterial/OrderKittingOverride siblings), which is a one-off swap
re-declared per product. That older mechanism is untouched by this migration.

Deliberately no category/size/dimension constraint on the pairing — substitutes are
curated judgment calls, not computed matches, and may cross material_category freely.

material_substitute_usage is the audit trail for actually choosing to use one, the
substitution analog of material_adjustments: which material was substituted, with what,
for which order/build, and by whom.

Revision ID: a1c4e0f9d823
Revises: b1e4f7a92c63
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c4e0f9d823'
down_revision: Union[str, Sequence[str], None] = 'b1e4f7a92c63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'material_substitutes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('material_id', sa.Integer(), nullable=False),
        sa.Column('substitute_material_id', sa.Integer(), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['substitute_material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'material_id', 'substitute_material_id', name='uq_material_substitutes_material_substitute'
        ),
        sa.CheckConstraint(
            'substitute_material_id != material_id', name='ck_material_substitutes_no_self_substitution'
        ),
    )

    op.create_table(
        'material_substitute_usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('material_id', sa.Integer(), nullable=False),
        sa.Column('substitute_material_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Numeric(14, 4), nullable=True),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('build_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['substitute_material_id'], ['materials.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['build_id'], ['builds.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('material_substitute_usage')
    op.drop_table('material_substitutes')
