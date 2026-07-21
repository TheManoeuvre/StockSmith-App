"""etsy listing sync status

Revision ID: f2a91b6d3e87
Revises: e5f8b3d1c7a4
Create Date: 2026-07-15 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2a91b6d3e87'
down_revision: Union[str, Sequence[str], None] = 'e5f8b3d1c7a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("listings", sa.Column("external_title", sa.String(), nullable=True))
    op.add_column("listings", sa.Column("external_state", sa.String(), nullable=True))
    op.add_column("listings", sa.Column("external_quantity", sa.Integer(), nullable=True))
    op.add_column("listings", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))

    # The original (platform, external_listing_id) constraint assumed one listing maps to
    # exactly one StockSmith row, but an Etsy listing with variations legitimately has
    # several StockSmith variants (each its own SKU/offering) pointing at the same
    # listing_id — that constraint would reject the second variant's check result.
    # (product_id, variant_id, platform) is the actual natural key for "one row per unit
    # per platform". Plain UNIQUE treats every NULL variant_id as distinct, which would
    # silently fail to dedupe no-variant products, so this uses an expression index that
    # coalesces variant_id to a sentinel instead.
    op.drop_constraint("uq_listings_platform_external_id", "listings", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_listings_product_variant_platform
        ON listings (product_id, COALESCE(variant_id, -1), platform)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX uq_listings_product_variant_platform")
    op.create_unique_constraint(
        "uq_listings_platform_external_id", "listings", ["platform", "external_listing_id"]
    )

    op.drop_column("listings", "last_checked_at")
    op.drop_column("listings", "external_quantity")
    op.drop_column("listings", "external_state")
    op.drop_column("listings", "external_title")
