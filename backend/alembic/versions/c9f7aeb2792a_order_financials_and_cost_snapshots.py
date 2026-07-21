"""order financials and cost snapshots

Revision ID: c9f7aeb2792a
Revises: 17fcb4346ec9
Create Date: 2026-07-17 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9f7aeb2792a'
down_revision: Union[str, Sequence[str], None] = '17fcb4346ec9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("grand_total", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("subtotal", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("shipping_charged", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("tax_charged", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("vat_charged", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("discount_amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("refunded_amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("currency", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("payment_fees", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("payment_net", sa.Numeric(10, 2), nullable=True))
    op.add_column("orders", sa.Column("payment_status", sa.String(), nullable=True))
    op.add_column("orders", sa.Column("financials_synced_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("order_lines", sa.Column("cost_per_unit_snapshot", sa.Numeric(14, 6), nullable=True))
    op.add_column("order_lines", sa.Column("kitting_cost_per_unit_snapshot", sa.Numeric(14, 6), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("order_lines", "kitting_cost_per_unit_snapshot")
    op.drop_column("order_lines", "cost_per_unit_snapshot")

    op.drop_column("orders", "financials_synced_at")
    op.drop_column("orders", "payment_status")
    op.drop_column("orders", "payment_net")
    op.drop_column("orders", "payment_fees")
    op.drop_column("orders", "currency")
    op.drop_column("orders", "refunded_amount")
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "vat_charged")
    op.drop_column("orders", "tax_charged")
    op.drop_column("orders", "shipping_charged")
    op.drop_column("orders", "subtotal")
    op.drop_column("orders", "grand_total")
