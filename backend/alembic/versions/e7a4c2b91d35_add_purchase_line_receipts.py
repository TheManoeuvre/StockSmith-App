"""add purchase line receipts, line closing, and the partially-received status

Receiving used to be a single flag on the order: every line arrived at once, at one
moment, or none of it had. A supplier who ships 6 of 10 now and 4 next month had no
representation, and the choice — receive it all early or leave it all open — was a choice
between two wrong stock figures and, worse, two wrong costs. Material cost is a weighted
average replayed in date order, so booking both deliveries at one date is not a rounding
difference; it is the wrong number, permanently.

material_purchase_receipts is one row per physical arrival. It, not purchases.status,
becomes what stock and cost are derived from, so each delivery lands in the timeline on
its own date.

total_cost on a receipt is nullable and normally NULL, meaning "this delivery's pro-rata
share of the line". That keeps a later invoice edit flowing through to history instead of
going stale, and it is what makes the backfill below exact rather than merely close.

material_purchases.closed_at records a short delivery — 7 of 10 turned up and the rest is
never coming. Shrinking qty to 7 would have done the same arithmetic while erasing the
fact that 10 was ordered, which is the one thing a buyer wants to see afterwards.

The status widening is the part that is easy to miss. purchases.status is a portable_enum,
which renders a bare VARCHAR sized to the longest member and no CHECK constraint (see
models/base.py). It is VARCHAR(8) today, the length of "received". "partially_received" is
18. SQLite ignores VARCHAR length and Postgres enforces it, so omitting this passes every
test on the developer's machine and rejects every partial receipt in production.

Revision ID: e7a4c2b91d35
Revises: b6c9e1a4f273
Create Date: 2026-08-19 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a4c2b91d35'
down_revision: Union[str, Sequence[str], None] = 'b6c9e1a4f273'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'material_purchase_receipts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('purchase_line_id', sa.Integer(), nullable=False),
        sa.Column('qty', sa.Numeric(14, 4), nullable=False),
        sa.Column('total_cost', sa.Numeric(14, 2), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('batch_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['purchase_line_id'], ['material_purchases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('qty > 0', name='ck_material_purchase_receipts_qty_positive'),
        sa.CheckConstraint(
            'total_cost IS NULL OR total_cost >= 0', name='ck_material_purchase_receipts_total_cost_nonneg'
        ),
    )
    op.create_index('ix_material_purchase_receipts_line', 'material_purchase_receipts', ['purchase_line_id'])
    op.create_index('ix_material_purchase_receipts_batch', 'material_purchase_receipts', ['batch_id'])

    op.add_column('material_purchases', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))

    # "received" is 8 characters, "partially_received" is 18. Batch mode because SQLite
    # cannot ALTER COLUMN a type; on Postgres this is a plain widening with no rewrite.
    with op.batch_alter_table('purchases') as batch:
        batch.alter_column(
            'status',
            existing_type=sa.String(length=8),
            type_=sa.String(length=18),
            existing_nullable=False,
        )

    _backfill_receipts()


def _backfill_receipts() -> None:
    """One synthetic receipt per line of every already-received order.

    The receipt covers the whole line and carries no explicit cost, so the replay's
    remainder sweep hands it line.total_cost untouched — the same Decimal the old code fed
    the weighted average. That is what makes this exact rather than approximately right:
    pro-rating qty/qty through Decimal division would round to the decimal context and
    shift every material's avg_unit_cost in the far digits.
    """
    conn = op.get_bind()

    # received_at should always be set on a received order, but a row where it isn't would
    # otherwise insert NULL into a NOT NULL column and abort with nothing explaining why.
    # Fall back to the order date and say so — it silently moves an event in the timeline,
    # which changes a weighted average, so it is worth knowing about.
    undated = conn.execute(
        sa.text("SELECT COUNT(*) FROM purchases WHERE status = 'received' AND received_at IS NULL")
    ).scalar_one()
    if undated:
        print(
            f"  [receipts backfill] {undated} received order(s) had no received_at; "
            f"dating their receipts from order_date instead."
        )

    conn.execute(
        sa.text(
            """
            INSERT INTO material_purchase_receipts
                (purchase_line_id, qty, total_cost, received_at, notes, batch_id)
            SELECT mp.id, mp.qty, NULL,
                   COALESCE(p.received_at, CAST(p.order_date AS TIMESTAMP)),
                   NULL, 'migrated'
            FROM material_purchases mp
            JOIN purchases p ON p.id = mp.purchase_id
            WHERE p.status = 'received'
            """
        )
    )

    # Structural invariants, checked here so a bad join aborts the upgrade rather than
    # quietly halving somebody's stock. The weighted average itself cannot be checked in
    # SQL — scripts/verify_receipt_backfill.py does that against a copy of the real
    # database, and asserts exact equality.
    mismatched_lines = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM material_purchases mp
            JOIN purchases p ON p.id = mp.purchase_id
            LEFT JOIN (
                SELECT purchase_line_id, COUNT(*) AS n, SUM(qty) AS total
                FROM material_purchase_receipts GROUP BY purchase_line_id
            ) r ON r.purchase_line_id = mp.id
            WHERE p.status = 'received' AND (r.n IS NULL OR r.n <> 1 OR r.total <> mp.qty)
            """
        )
    ).scalar_one()
    if mismatched_lines:
        raise RuntimeError(
            f"Receipt backfill is wrong: {mismatched_lines} received purchase line(s) did not "
            f"get exactly one receipt for their full quantity. Refusing to continue."
        )

    stray = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM material_purchase_receipts r
            JOIN material_purchases mp ON mp.id = r.purchase_line_id
            JOIN purchases p ON p.id = mp.purchase_id
            WHERE p.status <> 'received'
            """
        )
    ).scalar_one()
    if stray:
        raise RuntimeError(
            f"Receipt backfill is wrong: {stray} receipt(s) landed on orders that were never "
            f"received. Refusing to continue."
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Partial receipts have nowhere to go in the old model, and a line that was received
    # in part would come back as not received at all. Drop those receipts and reset the
    # status *before* narrowing the column: validate_strings=True means the old code
    # raises LookupError the moment it reads 'partially_received' back.
    conn.execute(
        sa.text(
            """
            DELETE FROM material_purchase_receipts
            WHERE purchase_line_id IN (
                SELECT mp.id FROM material_purchases mp
                JOIN purchases p ON p.id = mp.purchase_id
                WHERE p.status <> 'received'
            )
            """
        )
    )
    conn.execute(sa.text("UPDATE purchases SET status = 'ordered' WHERE status = 'partially_received'"))

    with op.batch_alter_table('purchases') as batch:
        batch.alter_column(
            'status',
            existing_type=sa.String(length=18),
            type_=sa.String(length=8),
            existing_nullable=False,
        )

    with op.batch_alter_table('material_purchases') as batch:
        batch.drop_column('closed_at')

    op.drop_index('ix_material_purchase_receipts_batch', table_name='material_purchase_receipts')
    op.drop_index('ix_material_purchase_receipts_line', table_name='material_purchase_receipts')
    op.drop_table('material_purchase_receipts')
