"""add purchases.supplier_order_number (and merge the two open heads)

The supplier's own reference for a purchase order — their PO/order/invoice number as
printed on the paperwork. Free text, optional, and NULL when not supplied. The purchases
list leads with it because it is the number a person quotes when chasing a delivery;
`purchases.id` stays the stable internal key.

This revision also closes the fork left by two migrations that both branched off
c7e2a9f3b104 on separate PRs (19e6521eb044 orders.manual_channel, f1a3c8e9d602 lead-time
in business days). Declaring both as down_revision merges them, so `alembic upgrade head`
resolves to a single head again.

Revision ID: a3f1c9e07b24
Revises: 19e6521eb044, f1a3c8e9d602
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f1c9e07b24'
down_revision: Union[str, Sequence[str], None] = ('19e6521eb044', 'f1a3c8e9d602')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'purchases',
        sa.Column('supplier_order_number', sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('purchases') as batch:
        batch.drop_column('supplier_order_number')
