"""add order_line_substitutions and relax order_lines ordered_qty constraint

Revision ID: b1e4f7a92c63
Revises: a2f6c918e4b7
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1e4f7a92c63'
down_revision: Union[str, Sequence[str], None] = 'a2f6c918e4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('order_line_substitutions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('original_line_id', sa.Integer(), nullable=False),
    sa.Column('new_line_id', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('reverted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reverted_qty', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['new_line_id'], ['order_lines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['original_line_id'], ['order_lines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('new_line_id', name='uq_order_line_substitutions_new_line'),
    )

    with op.batch_alter_table('order_lines') as batch_op:
        batch_op.drop_constraint('ck_order_lines_ordered_qty_positive', type_='check')
        batch_op.create_check_constraint('ck_order_lines_ordered_qty_nonneg', 'ordered_qty >= 0')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('order_lines') as batch_op:
        batch_op.drop_constraint('ck_order_lines_ordered_qty_nonneg', type_='check')
        batch_op.create_check_constraint('ck_order_lines_ordered_qty_positive', 'ordered_qty > 0')

    op.drop_table('order_line_substitutions')
