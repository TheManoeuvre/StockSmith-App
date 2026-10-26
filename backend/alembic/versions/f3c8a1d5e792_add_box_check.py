"""add kitting box check: material category flag and order confirmation columns

Packaging is capped to one per order for a multi-unit order, which also means an order whose
items use two different box sizes keeps BOTH boxes. This adds the pieces for a manual check:
a flag on material categories marking the ones whose duplicates need checking (e.g. a "Boxes"
category kept apart from labels and tape), and two columns on orders recording a person's
confirmation of which box(es) were used.

Existing rows start unflagged / unconfirmed, so nothing changes until a category is flagged.

Revision ID: f3c8a1d5e792
Revises: b4d8f2a6c139
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3c8a1d5e792'
down_revision: Union[str, Sequence[str], None] = 'b4d8f2a6c139'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'material_categories',
        sa.Column('kitting_check_duplicates', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column('orders', sa.Column('box_check_signature', sa.String(), nullable=True))
    op.add_column('orders', sa.Column('box_check_confirmed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('box_check_confirmed_at')
        batch.drop_column('box_check_signature')
    with op.batch_alter_table('material_categories') as batch:
        batch.drop_column('kitting_check_duplicates')
