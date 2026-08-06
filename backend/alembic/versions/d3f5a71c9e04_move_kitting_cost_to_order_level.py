"""move kitting cost from a per-line per-unit rate to the order-level kitting ledger

Packaging is consumed per ORDER, not per unit: auto_apply_multiunit_kitting_override caps a
multi-unit order's packaging-category materials at one for the whole order, and the
OrderKittingAllocation ledger consumes exactly that. But COGS charged
order_lines.kitting_cost_per_unit_snapshot x shipped_qty, so every multi-unit order was
over-charged for packaging (a 3-unit order with a GBP 1 box: 1 box consumed, GBP 3 charged).

This drops that column and gives the ledger its own unit cost, frozen the first time each
material is physically consumed for the order (reconcile_order_kitting) — the kitting analog
of orders.shipping_cost_snapshot's freeze-at-ship rule.

No data backfill. Existing ledger rows keep unit_cost_snapshot NULL and read through the
COALESCE(..., materials.avg_unit_cost) fallback in kitting.get_kitting_cogs_by_order. The
dropped column cannot supply a value here: it is a single blended figure across ALL of a
line's kitting materials and cannot be decomposed per material. Shipped orders whose ledger
never got written at all are repaired separately by
scripts/backfill_order_kitting_ledger.py.

Revision ID: d3f5a71c9e04
Revises: c7e1a9f04b21
Create Date: 2026-08-06 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f5a71c9e04'
down_revision: Union[str, Sequence[str], None] = 'c7e1a9f04b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'order_kitting_allocations',
        sa.Column('unit_cost_snapshot', sa.Numeric(precision=14, scale=6), nullable=True),
    )
    # Plain drop_column, deliberately not batch_alter_table: SQLite >= 3.35 supports native
    # DROP COLUMN and refuses only for a column named in a CHECK constraint — order_lines'
    # three CHECKs reference the qty columns only. Batch mode would recreate the table and
    # risk losing those named CHECKs through SQLite reflection.
    op.drop_column('order_lines', 'kitting_cost_per_unit_snapshot')


def downgrade() -> None:
    """Downgrade schema."""
    # Restored NULL-filled: the per-unit rates are not recoverable from the ledger, which
    # stores order-level totals. Downgrading and re-upgrading loses nothing that upgrading
    # had not already made unused.
    op.add_column(
        'order_lines',
        sa.Column('kitting_cost_per_unit_snapshot', sa.Numeric(precision=14, scale=6), nullable=True),
    )
    op.drop_column('order_kitting_allocations', 'unit_cost_snapshot')
