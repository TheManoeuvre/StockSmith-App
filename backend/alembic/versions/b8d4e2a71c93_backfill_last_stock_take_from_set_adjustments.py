"""backfill last_stock_take_at from existing 'set' adjustments

Data only — no schema change. The columns this fills were added in a7c3e1f09b52 and start
NULL, which reads as "never counted". On an existing database that makes the whole
catalogue due on the day the feature arrives, which is true but useless: a list containing
everything is not a priority order.

The database already knows better. A `set` adjustment IS a physical count — it is what
that mode has meant since it was added, and it stores the counted total in target_qty —
so the most recent one per item is a real answer to "when was this last counted". Items
that only ever had `adjust` rows, or no adjustments at all, are left NULL and keep reading
"never counted", which is equally true and equally useful.

Deliberately its own revision rather than riding along with a7c3e1f09b52's schema change.
It is one-way and touches user data, so it wants to be revertible and reviewable on its
own; mixing it into the column-add would make that impossible without also dropping the
columns.

MAX(created_at) rather than the row with the greatest id: created_at is what the app reads
and orders by everywhere else (services/abc.py::due_state), and on this table they agree
anyway since rows are only ever appended.

Products and variants are filled separately from the same table. stock_adjustments records
ownership as product_id plus a nullable variant_id — a row with variant_id set counted that
variant, one without counted the bare product — so each side filters on that rather than
assuming a product's adjustments belong to it alone. Getting this wrong would date every
variant of a product from a count of just one of them.

The downgrade is a no-op: there is nothing to restore these columns to except NULL, and
blanking them would destroy dates that later real stock takes had written. a7c3e1f09b52's
own downgrade drops the columns outright, which is the honest way back.

Because of that no-op, each UPDATE only fills rows that are still NULL. Without it, a
downgrade followed by a re-upgrade would re-run the backfill over dates that real stock
takes had written since, dragging them backwards to whenever the last manual Set happened.
On a first run every row is NULL, so the guard costs nothing and makes this safe to
re-apply.

Revision ID: b8d4e2a71c93
Revises: a7c3e1f09b52
Create Date: 2026-08-15 16:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d4e2a71c93'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1f09b52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        sa.text(
            """
            UPDATE materials
            SET last_stock_take_at = (
                SELECT MAX(ma.created_at)
                FROM material_adjustments ma
                WHERE ma.material_id = materials.id AND ma.mode = 'set'
            )
            WHERE last_stock_take_at IS NULL AND EXISTS (
                SELECT 1 FROM material_adjustments ma
                WHERE ma.material_id = materials.id AND ma.mode = 'set'
            )
            """
        )
    )

    # variant_id IS NULL: the adjustment counted the bare product, which is the row that
    # holds stock when a product has no active variants.
    op.execute(
        sa.text(
            """
            UPDATE products
            SET last_stock_take_at = (
                SELECT MAX(sa.created_at)
                FROM stock_adjustments sa
                WHERE sa.product_id = products.id AND sa.variant_id IS NULL AND sa.mode = 'set'
            )
            WHERE last_stock_take_at IS NULL AND EXISTS (
                SELECT 1 FROM stock_adjustments sa
                WHERE sa.product_id = products.id AND sa.variant_id IS NULL AND sa.mode = 'set'
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE product_variants
            SET last_stock_take_at = (
                SELECT MAX(sa.created_at)
                FROM stock_adjustments sa
                WHERE sa.variant_id = product_variants.id AND sa.mode = 'set'
            )
            WHERE last_stock_take_at IS NULL AND EXISTS (
                SELECT 1 FROM stock_adjustments sa
                WHERE sa.variant_id = product_variants.id AND sa.mode = 'set'
            )
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Intentionally empty — see the module docstring.
