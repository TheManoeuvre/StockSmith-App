"""orders and allocation

Revision ID: c1d4e8f2a9b3
Revises: b7c1e9f4a2d6
Create Date: 2026-07-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1d4e8f2a9b3'
down_revision: Union[str, Sequence[str], None] = 'b7c1e9f4a2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

order_status = postgresql.ENUM(
    "pending", "allocated", "shipped", "cancelled", name="order_status", create_type=False
)
allocation_event_type = postgresql.ENUM(
    "allocate", "deallocate", "ship", "auto_allocate", name="allocation_event_type", create_type=False
)
# Reuses the existing listing_platform type (created in the initial migration) — not
# created here, just referenced with create_type=False.
listing_platform = postgresql.ENUM("etsy", "ebay", "shopify", name="listing_platform", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    order_status.create(op.get_bind())
    allocation_event_type.create(op.get_bind())

    op.add_column("products", sa.Column("allocated_qty", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_products_allocated_qty_range", "products", "allocated_qty >= 0 AND allocated_qty <= current_stock"
    )

    op.add_column("product_variants", sa.Column("allocated_qty", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_product_variants_allocated_qty_range",
        "product_variants",
        "allocated_qty >= 0 AND allocated_qty <= current_stock",
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", listing_platform, nullable=True),
        sa.Column("external_order_id", sa.String(), nullable=True),
        sa.Column("status", order_status, nullable=False, server_default="pending"),
        sa.Column("buyer_name", sa.String(), nullable=True),
        sa.Column("buyer_note", sa.String(), nullable=True),
        sa.Column("order_placed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "external_order_id", name="uq_orders_platform_external_id"),
    )

    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("ordered_qty", sa.Integer(), nullable=False),
        sa.Column("allocated_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipped_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("external_line_id", sa.String(), nullable=True),
        sa.Column("sku", sa.String(), nullable=True),
        sa.Column("needs_mapping", sa.Boolean(), nullable=False, server_default="false"),
        sa.CheckConstraint("ordered_qty > 0", name="ck_order_lines_ordered_qty_positive"),
        sa.CheckConstraint(
            "allocated_qty >= 0 AND allocated_qty <= ordered_qty", name="ck_order_lines_allocated_qty_range"
        ),
        sa.CheckConstraint(
            "shipped_qty >= 0 AND shipped_qty <= allocated_qty", name="ck_order_lines_shipped_qty_range"
        ),
    )

    op.create_table(
        "allocation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_line_id", sa.Integer(), sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("event_type", allocation_event_type, nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("allocation_events")
    op.drop_table("order_lines")
    op.drop_table("orders")

    op.drop_constraint("ck_product_variants_allocated_qty_range", "product_variants", type_="check")
    op.drop_column("product_variants", "allocated_qty")

    op.drop_constraint("ck_products_allocated_qty_range", "products", type_="check")
    op.drop_column("products", "allocated_qty")

    allocation_event_type.drop(op.get_bind())
    order_status.drop(op.get_bind())
