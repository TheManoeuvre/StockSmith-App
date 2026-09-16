"""link shipping profiles to Etsy / eBay and split the buyer price per channel

Five additive, nullable columns on shipping_profiles (Stage 1 of
docs/plan-shipping-profile-marketplace-link.md):

  - price_etsy / price_ebay — the postage price charged to the buyer on that marketplace.
    NULL means "use price", so every existing row keeps exactly the margin it had. Product
    margin counts this price as revenue, which is why it now has to be right per channel.
  - etsy_shipping_profile_id / ebay_fulfillment_policy_id — the marketplace's own profile
    id, linking one local profile to one marketplace profile. Unique per platform (as a
    unique index, so the unlinked NULL majority don't collide). Once linked, the import
    action and later the scheduled refresh write price_<platform> from what the marketplace
    charges.

No backfill: linking is an explicit action and nothing is imported until asked.

batch_alter_table so the CHECK constraints and unique indexes apply on SQLite, which
cannot add a table constraint in place.

Revision ID: e3a9c7d51b04
Revises: e7c2a95d1b48
Create Date: 2026-09-14 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a9c7d51b04'
down_revision: Union[str, Sequence[str], None] = 'e7c2a95d1b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('shipping_profiles') as batch:
        batch.add_column(sa.Column('price_etsy', sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('price_ebay', sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column('etsy_shipping_profile_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('ebay_fulfillment_policy_id', sa.String(), nullable=True))
        batch.create_check_constraint(
            'ck_shipping_profiles_price_etsy_nonneg', 'price_etsy IS NULL OR price_etsy >= 0'
        )
        batch.create_check_constraint(
            'ck_shipping_profiles_price_ebay_nonneg', 'price_ebay IS NULL OR price_ebay >= 0'
        )
    op.create_index(
        'uq_shipping_profiles_etsy_shipping_profile_id',
        'shipping_profiles',
        ['etsy_shipping_profile_id'],
        unique=True,
    )
    op.create_index(
        'uq_shipping_profiles_ebay_fulfillment_policy_id',
        'shipping_profiles',
        ['ebay_fulfillment_policy_id'],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_shipping_profiles_ebay_fulfillment_policy_id', table_name='shipping_profiles')
    op.drop_index('uq_shipping_profiles_etsy_shipping_profile_id', table_name='shipping_profiles')
    with op.batch_alter_table('shipping_profiles') as batch:
        batch.drop_constraint('ck_shipping_profiles_price_ebay_nonneg', type_='check')
        batch.drop_constraint('ck_shipping_profiles_price_etsy_nonneg', type_='check')
        batch.drop_column('ebay_fulfillment_policy_id')
        batch.drop_column('etsy_shipping_profile_id')
        batch.drop_column('price_ebay')
        batch.drop_column('price_etsy')
