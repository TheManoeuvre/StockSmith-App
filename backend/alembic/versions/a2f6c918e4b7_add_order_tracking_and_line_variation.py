"""add order tracking number/carrier and order_lines.variation_text

Two independent additions surfaced by the eBay/Etsy "unused API data" review:

- orders.tracking_number / orders.carrier: the shipment tracking number and carrier,
  once the marketplace reports it (eBay's shipping_fulfillment trackingNumber/
  shippingCarrierCode, or the first entry of Etsy's receipt `shipments` array). A
  single pair per order — see order_sync._apply_financials.
- order_lines.variation_text: buyer-supplied personalization/customization text for a
  line (Etsy transaction variations). eBay has no equivalent, so this stays NULL for
  every eBay-sourced line.

Revision ID: a2f6c918e4b7
Revises: a1c7e4f92b56
Create Date: 2026-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2f6c918e4b7'
down_revision: Union[str, Sequence[str], None] = 'a1c7e4f92b56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('tracking_number', sa.String(), nullable=True))
    op.add_column('orders', sa.Column('carrier', sa.String(), nullable=True))
    op.add_column('order_lines', sa.Column('variation_text', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('order_lines') as batch:
        batch.drop_column('variation_text')
    with op.batch_alter_table('orders') as batch:
        batch.drop_column('carrier')
        batch.drop_column('tracking_number')
