"""backfill kitting material adjustment order

Revision ID: e2b6c0a4d8f1
Revises: d1a5b9e3f7c6
Create Date: 2026-07-21 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e2b6c0a4d8f1'
down_revision: Union[str, Sequence[str], None] = 'd1a5b9e3f7c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill order_id on material_adjustments created by kitting, for rows
    predating the same seed-data gap as the build backfill (see d1a5b9e3f7c6):
    reason already encodes "Order #<id> shipped (kitting)" but order_id was left
    null. Only fills rows whose order still exists — a row can also read
    order_id=NULL because the order was genuinely deleted (ON DELETE SET NULL),
    and those must stay unlinked."""
    op.execute(
        """
        UPDATE material_adjustments ma
        SET order_id = o.id
        FROM orders o
        WHERE ma.order_id IS NULL
          AND ma.reason ~ '^Order #\\d+ shipped \\(kitting\\)$'
          AND o.id = substring(ma.reason FROM '^Order #(\\d+) shipped \\(kitting\\)$')::int
        """
    )


def downgrade() -> None:
    """Backfilled values are indistinguishable from originally-set ones; no-op."""
    pass
