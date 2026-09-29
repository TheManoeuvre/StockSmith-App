"""add square platform and order fulfilment fields

Square in-person orders (see docs/plan-square-integration.md) need a new
ListingPlatform.square value, plus two order-level fields the existing
platforms have no equivalent for: fulfilment_method (collect vs delivery,
read from Square's order fulfilments) and collect_by (the pickup date for a
collect order). collect_by is deliberately its own column rather than
reusing ship_by_date: the sandbox spike found a Square SHIPMENT fulfilment
carries no date field at all, and ship_by_date already has specific,
unconditionally-refreshed semantics (the awaiting-orders sort, see
order_sync._apply_financials) that a "collect by" date shouldn't conflate
with. The 'square' platform value fits within the existing listing_platform
VARCHAR sizing (sized to 'shopify', 7 chars) so no column widening is
needed.

Revision ID: 95c15157d55b
Revises: d4a2f7c91b63
Create Date: 2026-09-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95c15157d55b'
down_revision: Union[str, Sequence[str], None] = 'd4a2f7c91b63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FULFILMENT_METHOD = sa.Enum('collect', 'delivery', name='order_fulfilment_method', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    # 'square' is added to the ListingPlatform Python enum in app/models/listing.py, not here:
    # portable_enum renders a plain VARCHAR sized to the longest member already baked into each
    # table's platform column ('shopify', 7 chars), and 'square' (6 chars) already fits — no
    # column width change needed.
    op.add_column('orders', sa.Column('fulfilment_method', _FULFILMENT_METHOD, nullable=True))
    op.add_column('orders', sa.Column('collect_by', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'collect_by')
    op.drop_column('orders', 'fulfilment_method')
