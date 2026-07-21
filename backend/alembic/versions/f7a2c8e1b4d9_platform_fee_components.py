"""platform fee components

Revision ID: f7a2c8e1b4d9
Revises: e6b83a5f9c14
Create Date: 2026-07-16 12:00:00.000000

Seeds Etsy UK / eBay UK fee components as researched in July 2026 — these are
point-in-time rates and should be periodically re-verified against each platform's own
fee pages (see the Settings "Margin fee source" panel).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f7a2c8e1b4d9'
down_revision: Union[str, Sequence[str], None] = 'e6b83a5f9c14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

listing_platform = postgresql.ENUM("etsy", "ebay", "shopify", name="listing_platform", create_type=False)
fee_basis = postgresql.ENUM(
    "sale_price", "sale_price_plus_shipping", "fees_subtotal", name="fee_basis", create_type=False
)
margin_fee_source = postgresql.ENUM("manual", "etsy", "ebay", name="margin_fee_source", create_type=False)

_fee_components_table = sa.table(
    "platform_fee_components",
    sa.column("platform", listing_platform),
    sa.column("name", sa.String),
    sa.column("basis", fee_basis),
    sa.column("rate_percent", sa.Numeric),
    sa.column("fixed_amount", sa.Numeric),
    sa.column("display_order", sa.Integer),
    sa.column("enabled", sa.Boolean),
)


def upgrade() -> None:
    """Upgrade schema."""
    fee_basis.create(op.get_bind())
    margin_fee_source.create(op.get_bind())

    op.create_table(
        "platform_fee_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", listing_platform, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("basis", fee_basis, nullable=False),
        sa.Column("rate_percent", sa.Numeric(6, 3), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "rate_percent IS NOT NULL OR fixed_amount IS NOT NULL", name="ck_platform_fee_components_has_value"
        ),
    )

    op.create_table(
        "margin_fee_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fee_source", margin_fee_source, nullable=False, server_default="manual"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute("INSERT INTO margin_fee_config (id, fee_source) VALUES (1, 'manual')")

    op.bulk_insert(
        _fee_components_table,
        [
            # Etsy UK — researched July 2026.
            {
                "platform": "etsy",
                "name": "Transaction fee",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 6.5,
                "fixed_amount": None,
                "display_order": 1,
                "enabled": True,
            },
            {
                "platform": "etsy",
                "name": "Payment processing",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 4.0,
                "fixed_amount": 0.20,
                "display_order": 2,
                "enabled": True,
            },
            {
                "platform": "etsy",
                "name": "Regulatory operating fee",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 0.48,
                "fixed_amount": None,
                "display_order": 3,
                "enabled": True,
            },
            {
                "platform": "etsy",
                "name": "VAT on fees",
                "basis": "fees_subtotal",
                "rate_percent": 20.0,
                "fixed_amount": None,
                "display_order": 4,
                "enabled": True,
            },
            {
                "platform": "etsy",
                "name": "Offsite ads (situational)",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 12.0,
                "fixed_amount": None,
                "display_order": 5,
                "enabled": False,
            },
            # eBay UK — researched July 2026. Final value fee is category-dependent
            # (~6.9-14.9%); 12.8% is the common business-seller rate, seeded as a
            # representative default. Per-order fee is tiered by order value (30p at or
            # below £10, 40p above) — seeded with the lower tier.
            {
                "platform": "ebay",
                "name": "Final value fee",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 12.8,
                "fixed_amount": None,
                "display_order": 1,
                "enabled": True,
            },
            {
                "platform": "ebay",
                "name": "Per-order fee",
                "basis": "sale_price_plus_shipping",
                "rate_percent": None,
                "fixed_amount": 0.30,
                "display_order": 2,
                "enabled": True,
            },
            {
                "platform": "ebay",
                "name": "Regulatory operating fee",
                "basis": "sale_price_plus_shipping",
                "rate_percent": 0.35,
                "fixed_amount": None,
                "display_order": 3,
                "enabled": True,
            },
            {
                "platform": "ebay",
                "name": "VAT on fees",
                "basis": "fees_subtotal",
                "rate_percent": 20.0,
                "fixed_amount": None,
                "display_order": 4,
                "enabled": True,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("margin_fee_config")
    op.drop_table("platform_fee_components")
    bind = op.get_bind()
    margin_fee_source.drop(bind, checkfirst=True)
    fee_basis.drop(bind, checkfirst=True)
