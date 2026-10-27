"""add order_lines.external_listing_id

The marketplace listing a line was sold from (Etsy transaction.listing_id), kept for
attribution so sales can later be compared per listing when several listings share a SKU.
Matching is unchanged — still by SKU.

Plain nullable column, no backfill — historical lines don't have the listing id stored and
NULL means "not captured".

Revision ID: f3a9d1c47b26
Revises: e2b7c4a91d58
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9d1c47b26'
down_revision: Union[str, Sequence[str], None] = 'e2b7c4a91d58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('order_lines', sa.Column('external_listing_id', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('order_lines') as batch:
        batch.drop_column('external_listing_id')
