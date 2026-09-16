"""scheduled shipping price refresh: connection cadence and a price event log

Stage 3 of docs/plan-shipping-profile-marketplace-link.md.

  - platform_connections.shipping_price_refresh_hours (default 24) and
    last_shipping_price_refresh_at — how often the background order-sync tick also
    re-reads the marketplace's shipping profiles and refreshes every linked local
    profile's price_<platform>, and when it last did.
  - shipping_profile_price_events — append-only record of every change to a per-channel
    buyer price (old, new, when, and whether the refresh, the manual import or a user
    edit made it). Product margin counts that price as revenue and the refresh overwrites
    without asking, so the "why did margin move" question has to be answerable.

Additive; existing rows get the 24h default and no history. batch_alter_table for the
SQLite add-column-with-default path.

Revision ID: f4b8d2c17a93
Revises: e3a9c7d51b04
Create Date: 2026-09-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b8d2c17a93'
down_revision: Union[str, Sequence[str], None] = 'e3a9c7d51b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('platform_connections') as batch:
        batch.add_column(
            sa.Column('shipping_price_refresh_hours', sa.Integer(), nullable=False, server_default='24')
        )
        batch.add_column(sa.Column('last_shipping_price_refresh_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'shipping_profile_price_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shipping_profile_id', sa.Integer(), nullable=False),
        sa.Column(
            'platform',
            sa.Enum('etsy', 'ebay', 'shopify', name='listing_platform', native_enum=False),
            nullable=False,
        ),
        sa.Column('old_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('new_price', sa.Numeric(10, 2), nullable=True),
        sa.Column(
            'source',
            sa.Enum('sync', 'manual_import', 'user_edit', name='shipping_price_event_source', native_enum=False),
            nullable=False,
        ),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['shipping_profile_id'], ['shipping_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_shipping_profile_price_events_shipping_profile_id',
        'shipping_profile_price_events',
        ['shipping_profile_id'],
    )
    op.create_index(
        'ix_shipping_profile_price_events_changed_at', 'shipping_profile_price_events', ['changed_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_shipping_profile_price_events_changed_at', table_name='shipping_profile_price_events')
    op.drop_index('ix_shipping_profile_price_events_shipping_profile_id', table_name='shipping_profile_price_events')
    op.drop_table('shipping_profile_price_events')
    with op.batch_alter_table('platform_connections') as batch:
        batch.drop_column('last_shipping_price_refresh_at')
        batch.drop_column('shipping_price_refresh_hours')
