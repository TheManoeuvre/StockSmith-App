"""platform connections

Revision ID: d3e7a1c9f5b8
Revises: c1d4e8f2a9b3
Create Date: 2026-07-10 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd3e7a1c9f5b8'
down_revision: Union[str, Sequence[str], None] = 'c1d4e8f2a9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reuses the existing listing_platform type (created in the initial migration) — not
# created here, just referenced with create_type=False.
listing_platform = postgresql.ENUM("etsy", "ebay", "shopify", name="listing_platform", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "platform_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", listing_platform, nullable=False),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.String(), nullable=True),
        sa.Column("etsy_shop_id", sa.String(), nullable=True),
        sa.Column("last_orders_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("platform", name="uq_platform_connections_platform"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("platform_connections")
