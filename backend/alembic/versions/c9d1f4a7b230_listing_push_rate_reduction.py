"""listing push rate reduction: last_pushed watermark + api usage accounting

Two additions behind Stages 2-3 of docs/plan-listing-push-rate-reduction.md:

  - listings.last_pushed_qty / last_pushed_at — the authoritative "what quantity did we
    last send this marketplace, and when" watermark. services/listing_push._push_now
    skips a push outright when last_pushed_qty already equals the resolved quantity,
    which is what stops a shared-material change from re-pushing a large slice of the
    catalogue (the 2026-09-07 API-budget blowout). Distinct from last_synced_qty, which
    services/kitting also writes with a different meaning.

  - platform_api_usage — one row per (platform, UTC date) counting every marketplace API
    round-trip, so listing_push and the reconcile sweep can stand down before they
    exhaust a platform's daily budget and starve order sync.

Both are additive and nullable/empty on existing rows; no backfill.

Revision ID: c9d1f4a7b230
Revises: b4d2f8c1a6e9
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d1f4a7b230'
down_revision: Union[str, Sequence[str], None] = 'b4d2f8c1a6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LISTING_PLATFORM = sa.Enum('etsy', 'ebay', 'shopify', name='listing_platform', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('listings', sa.Column('last_pushed_qty', sa.Integer(), nullable=True))
    op.add_column('listings', sa.Column('last_pushed_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'platform_api_usage',
        sa.Column('platform', _LISTING_PLATFORM, nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('call_count', sa.Integer(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('platform', 'usage_date'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('platform_api_usage')
    with op.batch_alter_table('listings') as batch:
        batch.drop_column('last_pushed_at')
        batch.drop_column('last_pushed_qty')
