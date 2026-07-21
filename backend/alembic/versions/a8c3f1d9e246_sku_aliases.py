"""sku aliases

Revision ID: a8c3f1d9e246
Revises: f2a91b6d3e87
Create Date: 2026-07-15 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a8c3f1d9e246'
down_revision: Union[str, Sequence[str], None] = 'f2a91b6d3e87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

listing_platform = postgresql.ENUM("etsy", "ebay", "shopify", name="listing_platform", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sku_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", listing_platform, nullable=False),
        sa.Column("external_sku", sa.String(), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "external_sku", name="uq_sku_aliases_platform_external_sku"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sku_aliases")
