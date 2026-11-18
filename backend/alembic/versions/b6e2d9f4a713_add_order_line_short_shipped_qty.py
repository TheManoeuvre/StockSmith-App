"""add short_shipped_qty to order_lines

Units an order was marked as shipping that never physically left (see
allocation.correct_shipped_qty). Existing lines have none.

Revision ID: b6e2d9f4a713
Revises: 8c9f5a2c7717
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b6e2d9f4a713'
down_revision: Union[str, Sequence[str], None] = '8c9f5a2c7717'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('order_lines') as batch_op:
        batch_op.add_column(sa.Column('short_shipped_qty', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_check_constraint(
            'ck_order_lines_short_shipped_qty_range',
            'short_shipped_qty >= 0 AND allocated_qty + short_shipped_qty <= ordered_qty',
        )


def downgrade() -> None:
    with op.batch_alter_table('order_lines') as batch_op:
        batch_op.drop_constraint('ck_order_lines_short_shipped_qty_range', type_='check')
        batch_op.drop_column('short_shipped_qty')
