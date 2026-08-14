"""add listing profiles, per-product platform settings, and listing copy fields

Creating a marketplace listing needs facts inventory management never cared about:
Etsy requires who_made, when_made and taxonomy_id before it will accept a draft at all,
plus a shipping profile for any physical item, and eBay wants a category, a condition and
three business policies. None of it existed here, because until now nothing in StockSmith
ever created a listing — every write was an update to something that already existed.

Modelled as named profiles rather than as a shop default plus per-product override
columns. Products that differ tend to differ together: a different taxonomy usually
arrives with a different shipping profile and processing time. Picking one profile makes
an incoherent half-overridden combination unrepresentable, and it matches ShippingProfile,
which the app already models as a named row referenced by FK.

Nothing is seeded, deliberately. A wrong fulfillment policy id does not fail loudly — it
silently mis-ships — so an empty field that blocks a draft with a clear message beats a
plausible guess every time.

`product_platform_settings.is_target` is added now but read later. target_platforms()
currently derives the target set from connections and live listings and will honour this
column when a row exists; adding it here keeps that a one-line change rather than a second
migration.

products.listing_title / listing_description split listing copy away from the inventory
name and description. `name` is what this is called at the bench; a listing title is a
different artefact with a character budget (Etsy 140, eBay 80) and search to satisfy.
Both are nullable and fall back to name/description, so no backfill is needed and nothing
changes for a product whose copy nobody has written yet.

Revision ID: c9d2e4a7f610
Revises: b3f7c1d92a48
Create Date: 2026-08-14 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d2e4a7f610'
down_revision: Union[str, Sequence[str], None] = 'b3f7c1d92a48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLATFORM = sa.Enum('etsy', 'ebay', 'shopify', name='listing_platform', native_enum=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'listing_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('platform', _PLATFORM, nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('etsy_taxonomy_id', sa.Integer(), nullable=True),
        sa.Column('etsy_who_made', sa.String(), nullable=True),
        sa.Column('etsy_when_made', sa.String(), nullable=True),
        sa.Column('etsy_is_supply', sa.Boolean(), nullable=True),
        sa.Column('etsy_shipping_profile_id', sa.Integer(), nullable=True),
        sa.Column('etsy_return_policy_id', sa.Integer(), nullable=True),
        sa.Column('etsy_shop_section_id', sa.Integer(), nullable=True),
        sa.Column('etsy_processing_min', sa.Integer(), nullable=True),
        sa.Column('etsy_processing_max', sa.Integer(), nullable=True),
        sa.Column('ebay_category_id', sa.String(), nullable=True),
        sa.Column('ebay_condition', sa.String(), nullable=True),
        sa.Column('ebay_fulfillment_policy_id', sa.String(), nullable=True),
        sa.Column('ebay_payment_policy_id', sa.String(), nullable=True),
        sa.Column('ebay_return_policy_id', sa.String(), nullable=True),
        sa.Column('ebay_merchant_location_key', sa.String(), nullable=True),
        sa.Column('ebay_marketplace_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', 'name', name='uq_listing_profiles_platform_name'),
    )
    # Partial index: at most one default per platform. A plain unique on
    # (platform, is_default) would also forbid a second *non*-default profile, since they
    # all share is_default = false.
    op.create_index(
        'uq_listing_profiles_platform_default',
        'listing_profiles',
        ['platform'],
        unique=True,
        sqlite_where=sa.text('is_default IS 1'),
        postgresql_where=sa.text('is_default'),
    )

    op.create_table(
        'product_platform_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('platform', _PLATFORM, nullable=False),
        sa.Column('listing_profile_id', sa.Integer(), nullable=True),
        sa.Column('is_target', sa.Boolean(), nullable=True),
        sa.Column('listing_title', sa.String(), nullable=True),
        sa.Column('listing_description', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        # SET NULL rather than CASCADE: deleting a profile should drop the products back to
        # the platform default, not delete their settings and any listing copy with them.
        sa.ForeignKeyConstraint(['listing_profile_id'], ['listing_profiles.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('product_id', 'platform', name='uq_product_platform_settings_product_platform'),
    )

    # Plain column adds, so no batch_alter_table needed — that is only required on SQLite
    # for constraint changes and column drops.
    op.add_column('products', sa.Column('listing_title', sa.String(), nullable=True))
    op.add_column('products', sa.Column('listing_description', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('products') as batch:
        batch.drop_column('listing_description')
        batch.drop_column('listing_title')
    op.drop_table('product_platform_settings')
    op.drop_index('uq_listing_profiles_platform_default', table_name='listing_profiles')
    op.drop_table('listing_profiles')
