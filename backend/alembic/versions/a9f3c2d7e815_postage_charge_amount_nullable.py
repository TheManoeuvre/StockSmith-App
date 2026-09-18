"""order_postage_charges.amount nullable

A label the marketplace confirms but won't cost per order. eBay's bulk "buy labels" flow
books a single SHIPPING_LABEL transaction for the whole batch — batch total, no orderId —
and returns it for every order in the batch, so storing its amount as one order's postage
charged that order for everyone's labels (order 04-15163-59902 carried £21.90 for six
£3.65 labels). Such a label is now stored with amount NULL: it keeps its place in the
sequence, and profit stays on the shipping-profile estimate. See
OrderPostageCharge / EbayAdapter._parse_shipping_labels.

Existing rows aren't touched here — the database can't tell a bulk label from a real
one. The sync corrects a stored label the next time it sees it (apply_postage_charges),
and scripts/backfill_postage_charges.py --all re-checks every shipped order on demand.

Revision ID: a9f3c2d7e815
Revises: d4e8b2f61c93
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9f3c2d7e815'
down_revision: Union[str, Sequence[str], None] = 'd4e8b2f61c93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('order_postage_charges') as batch:
        batch.alter_column('amount', existing_type=sa.Numeric(precision=10, scale=2), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # A NULL amount can't survive NOT NULL; 0 is the least-wrong stand-in (profit then
    # charges nothing for the label, rather than the batch total).
    op.execute("UPDATE order_postage_charges SET amount = 0 WHERE amount IS NULL")
    with op.batch_alter_table('order_postage_charges') as batch:
        batch.alter_column('amount', existing_type=sa.Numeric(precision=10, scale=2), nullable=False)
