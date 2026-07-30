"""add unified stock history and failed-build tracking

Adds:
- builds.qty_failed (units attempted but not produced) and relaxes builds' check
  constraint so a build row can represent a pure-failure run (qty_built=0, qty_failed>0).
- build_failed_consumptions: per-BOM-line record of what was actually consumed for a
  build's failed qty, plus a frozen unit-cost snapshot for the planned yield-cost work.
- product_stock_events: unified, append-only ledger (builds, stock adjustments, order
  fulfillment) backing the combined "Stock" history view — replaces two separate history
  tables/UI sections with one, and adds order fulfillment as a stock-affecting event for
  the first time.

Revision ID: 4a7c18daa1eb
Revises: 9c4f21ab7d30
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a7c18daa1eb'
down_revision: Union[str, Sequence[str], None] = '9c4f21ab7d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch mode: SQLite can't ALTER a CHECK constraint in place, so this rebuilds the
    # table under the hood — existing rows (all qty_built > 0 already) satisfy the new,
    # looser constraint unchanged.
    with op.batch_alter_table('builds', schema=None) as batch_op:
        batch_op.add_column(sa.Column('qty_failed', sa.Integer(), server_default='0', nullable=False))
        batch_op.drop_constraint('ck_builds_qty_built_positive', type_='check')
        batch_op.create_check_constraint('ck_builds_qty_built_nonneg', 'qty_built >= 0')
        batch_op.create_check_constraint('ck_builds_qty_failed_nonneg', 'qty_failed >= 0')
        batch_op.create_check_constraint('ck_builds_qty_built_or_failed_positive', 'qty_built > 0 OR qty_failed > 0')

    op.create_table(
        'build_failed_consumptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('build_id', sa.Integer(), nullable=False),
        sa.Column('material_id', sa.Integer(), nullable=False),
        sa.Column('was_consumed', sa.Boolean(), nullable=False),
        sa.Column('qty_consumed', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('unit_cost_snapshot', sa.Numeric(14, 6), nullable=True),
        sa.CheckConstraint('qty_consumed >= 0', name='ck_build_failed_consumptions_qty_consumed_nonneg'),
        sa.ForeignKeyConstraint(['build_id'], ['builds.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('build_id', 'material_id', name='uq_build_failed_consumptions_build_material'),
    )

    op.create_table(
        'product_stock_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('variant_id', sa.Integer(), nullable=True),
        sa.Column(
            'event_type',
            sa.Enum('build_success', 'build_failed', 'adjustment', 'order_fulfillment', name='product_stock_event_type'),
            nullable=False,
        ),
        sa.Column('qty_delta', sa.Integer(), nullable=False),
        sa.Column('running_balance', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('source_build_id', sa.Integer(), nullable=True),
        sa.Column('source_adjustment_id', sa.Integer(), nullable=True),
        sa.Column('source_order_line_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_build_id'], ['builds.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_adjustment_id'], ['stock_adjustments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_order_line_id'], ['order_lines.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('product_stock_events')
    op.drop_table('build_failed_consumptions')
    with op.batch_alter_table('builds', schema=None) as batch_op:
        batch_op.drop_constraint('ck_builds_qty_built_or_failed_positive', type_='check')
        batch_op.drop_constraint('ck_builds_qty_failed_nonneg', type_='check')
        batch_op.drop_constraint('ck_builds_qty_built_nonneg', type_='check')
        batch_op.create_check_constraint('ck_builds_qty_built_positive', 'qty_built > 0')
        batch_op.drop_column('qty_failed')
