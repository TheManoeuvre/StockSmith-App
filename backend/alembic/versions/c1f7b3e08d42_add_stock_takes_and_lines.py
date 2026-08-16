"""add stock takes, their lines, and last_stock_take_id

The counting session itself. Phase A recorded *when* something was last counted; this
records the count that did it — what was expected, what was found, and what happened to
the difference.

stock_take_lines carries a snapshot rather than reading live figures, and that is the
whole mechanism for detecting movement mid-count: expected_qty is what the system believed
when the take started, so comparing it against the live quantity at approval is what
distinguishes "you counted it wrong" from "something moved while you were counting". It
must never be refreshed.

allocated_qty_at_start exists because allocated stock is physically absent. Units picked
for an open order are boxed and off the shelf but still counted in current_stock until the
order ships, so a shelf count legitimately comes up short by that much. Snapshotting it
alongside means the count sheet, the CSV and the review all describe one moment.

counted_qty is nullable on purpose. NULL is "didn't get to this one", 0 is "counted it,
there are none" — and the rule that a blank line adjusts nothing and does not restart the
item's counting clock depends entirely on telling those apart.

The product uniqueness is an expression index over COALESCE(variant_id, -1) rather than a
three-column UniqueConstraint. SQLite and Postgres both treat NULLs as distinct in a unique
index, so a plain constraint would allow two (take, product, NULL) rows — and NULL
variant_id is the common case, not the exotic one.

last_stock_take_id is added to the three stock-holding tables now that stock_takes exists
(Phase A added the date alone, deliberately, since there was no table to point at). NULL
covers two true things: never counted, and counted by hand via a Set adjustment rather
than in a take. The date is what the cadence reads; this only says where it came from.

Revision ID: c1f7b3e08d42
Revises: b8d4e2a71c93
Create Date: 2026-08-15 17:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1f7b3e08d42'
down_revision: Union[str, Sequence[str], None] = 'b8d4e2a71c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TAKE_STATUS = sa.Enum('open', 'closed', name='stock_take_status', native_enum=False)
_LINE_STATUS = sa.Enum(
    'pending', 'counted', 'applied', 'conflict', 'accepted_system', 'skipped',
    name='stock_take_line_status',
    native_enum=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'stock_takes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', _TAKE_STATUS, nullable=False),
        sa.Column('includes_materials', sa.Boolean(), nullable=False),
        sa.Column('includes_products', sa.Boolean(), nullable=False),
        sa.Column('overdue_only', sa.Boolean(), nullable=False),
        sa.Column('scope_description', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'stock_take_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stock_take_id', sa.Integer(), nullable=False),
        sa.Column('material_id', sa.Integer(), nullable=True),
        sa.Column('product_id', sa.Integer(), nullable=True),
        sa.Column('variant_id', sa.Integer(), nullable=True),
        sa.Column('expected_qty', sa.Numeric(14, 4), nullable=False),
        sa.Column('allocated_qty_at_start', sa.Numeric(14, 4), nullable=True),
        sa.Column('counted_qty', sa.Numeric(14, 4), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('status', _LINE_STATUS, nullable=False),
        sa.Column('system_qty_at_approval', sa.Numeric(14, 4), nullable=True),
        sa.Column('conflict_reason', sa.String(), nullable=True),
        sa.Column('material_adjustment_id', sa.Integer(), nullable=True),
        sa.Column('stock_adjustment_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint(
            '(material_id IS NOT NULL AND product_id IS NULL AND variant_id IS NULL)'
            ' OR (material_id IS NULL AND product_id IS NOT NULL)',
            name='ck_stock_take_lines_exactly_one_owner',
        ),
        sa.CheckConstraint('counted_qty IS NULL OR counted_qty >= 0', name='ck_stock_take_lines_counted_nonneg'),
        sa.ForeignKeyConstraint(['stock_take_id'], ['stock_takes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['material_adjustment_id'], ['material_adjustments.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['stock_adjustment_id'], ['stock_adjustments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stock_take_id', 'material_id', name='uq_stock_take_lines_take_material'),
    )
    # Raw DDL: Alembic's create_index can't express an expression column portably, and the
    # COALESCE is the point — see the module docstring.
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_stock_take_lines_take_product_variant"
            " ON stock_take_lines (stock_take_id, product_id, COALESCE(variant_id, -1))"
        )
    )
    op.create_index('ix_stock_take_lines_stock_take_id', 'stock_take_lines', ['stock_take_id'])
    # The unresolved-variances view filters on this across every closed take, so it is the
    # one lookup that isn't scoped to a single take id.
    op.create_index('ix_stock_take_lines_status', 'stock_take_lines', ['status'])

    # batch_alter_table because each add carries a foreign key, and SQLite has no ADD
    # CONSTRAINT — the same reason a7c3e1f09b52 needed it for products.product_type_id.
    for table in ('materials', 'products', 'product_variants'):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column(
                    'last_stock_take_id',
                    sa.Integer(),
                    sa.ForeignKey('stock_takes.id', ondelete='SET NULL', name=f'fk_{table}_last_stock_take_id'),
                    nullable=True,
                )
            )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ('product_variants', 'products', 'materials'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('last_stock_take_id')
    op.drop_index('ix_stock_take_lines_status', table_name='stock_take_lines')
    op.drop_index('ix_stock_take_lines_stock_take_id', table_name='stock_take_lines')
    op.drop_index('uq_stock_take_lines_take_product_variant', table_name='stock_take_lines')
    op.drop_table('stock_take_lines')
    op.drop_table('stock_takes')
