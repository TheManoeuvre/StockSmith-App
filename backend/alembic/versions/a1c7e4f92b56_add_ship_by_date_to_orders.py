"""add ship_by_date to orders

The marketplace's own fulfillment deadline — Etsy's Receipt.expected_ship_date, or the
earliest eBay lineItemFulfillmentInstructions.shipByDate across an order's line items.
Nullable: unset for manual orders and for any synced order the marketplace didn't report
one for.

Revision ID: a1c7e4f92b56
Revises: fdc3ea941e39
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c7e4f92b56'
down_revision: Union[str, Sequence[str], None] = 'fdc3ea941e39'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('ship_by_date', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'ship_by_date')
