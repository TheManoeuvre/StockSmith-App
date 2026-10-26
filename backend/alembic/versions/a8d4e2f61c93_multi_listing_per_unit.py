"""allow several marketplace listings per product/variant

Several listings can share one SKU (A/B tests, a free-shipping variant) and draw on one
stock pool. A unit keeps one *primary* listings row — the one every single-listing code
path reads — and gains an additional row per further listing holding the SKU.

- listings.is_primary (existing rows are all primary) and listings.role (display label).
- The unique index on (product, variant, platform) becomes partial on is_primary, and a
  second unique index stops one marketplace listing being linked to a unit twice.
- platform_listing_pushes.listing_id, backfilled from the unit's single existing row, so
  "latest push attempt per listing" can tell sibling listings apart.

Revision ID: a8d4e2f61c93
Revises: f3a9d1c47b26
Create Date: 2026-10-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8d4e2f61c93'
down_revision: Union[str, Sequence[str], None] = 'f3a9d1c47b26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'listings', sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column('listings', sa.Column('role', sa.String(), nullable=True))

    op.drop_index('uq_listings_product_variant_platform', table_name='listings')
    op.create_index(
        'uq_listings_product_variant_platform',
        'listings',
        ['product_id', sa.text('coalesce(variant_id, -1)'), 'platform'],
        unique=True,
        sqlite_where=sa.text('is_primary'),
        postgresql_where=sa.text('is_primary'),
    )
    op.create_index(
        'uq_listings_unit_platform_external_listing',
        'listings',
        ['product_id', sa.text('coalesce(variant_id, -1)'), 'platform', 'external_listing_id'],
        unique=True,
        sqlite_where=sa.text('external_listing_id IS NOT NULL'),
        postgresql_where=sa.text('external_listing_id IS NOT NULL'),
    )

    with op.batch_alter_table('platform_listing_pushes') as batch:
        batch.add_column(sa.Column('listing_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_platform_listing_pushes_listing_id', 'listings', ['listing_id'], ['id'], ondelete='SET NULL'
        )
    # Every unit had exactly one listings row until now, so the match is unambiguous.
    op.execute(
        """
        UPDATE platform_listing_pushes SET listing_id = (
            SELECT l.id FROM listings l
            WHERE l.platform = platform_listing_pushes.platform
              AND l.product_id = platform_listing_pushes.product_id
              AND coalesce(l.variant_id, -1) = coalesce(platform_listing_pushes.variant_id, -1)
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('platform_listing_pushes') as batch:
        batch.drop_constraint('fk_platform_listing_pushes_listing_id', type_='foreignkey')
        batch.drop_column('listing_id')

    # Additional rows would violate the original unique index, so drop them first.
    op.execute('DELETE FROM listings WHERE is_primary = false')
    op.drop_index('uq_listings_unit_platform_external_listing', table_name='listings')
    op.drop_index('uq_listings_product_variant_platform', table_name='listings')
    # Columns first, index last: batch_alter_table can't reflect an expression index, so
    # it would silently drop one that existed when the table was rebuilt.
    with op.batch_alter_table('listings') as batch:
        batch.drop_column('role')
        batch.drop_column('is_primary')
    op.create_index(
        'uq_listings_product_variant_platform',
        'listings',
        ['product_id', sa.text('coalesce(variant_id, -1)'), 'platform'],
        unique=True,
    )
