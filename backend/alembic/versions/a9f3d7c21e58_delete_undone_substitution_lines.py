"""relax order_line_substitutions.new_line_id so undo can delete the emptied line

Revision ID: a9f3d7c21e58
Revises: e7c2a95d1b48
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9f3d7c21e58'
down_revision: Union[str, Sequence[str], None] = 'e7c2a95d1b48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The original migration created this FK unnamed; giving batch mode a naming convention is
# how Alembic lets an unnamed SQLite constraint be addressed for drop_constraint.
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade() -> None:
    """Upgrade schema."""
    # Undoing a substitution used to leave its replacement line behind at ordered_qty 0 with
    # nothing pointing at it — a blank grey row on the order. Undo now deletes that line,
    # which needs the substitution's own new_line_id to survive the delete (SET NULL, not
    # CASCADE) so reverted_at/reverted_qty stay as the audit record.
    with op.batch_alter_table('order_line_substitutions', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_order_line_substitutions_new_line_id_order_lines', type_='foreignkey')
        batch_op.alter_column('new_line_id', existing_type=sa.Integer(), nullable=True)
        batch_op.create_foreign_key(
            'fk_order_line_substitutions_new_line_id_order_lines',
            'order_lines', ['new_line_id'], ['id'], ondelete='SET NULL',
        )

    # Clean up the blank rows already left behind: lines created by a since-undone
    # substitution, holding no demand, that no still-active substitution was split from.
    bind = op.get_bind()
    ghost_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT ol.id
                FROM order_lines ol
                JOIN order_line_substitutions s ON s.new_line_id = ol.id
                WHERE s.reverted_at IS NOT NULL
                  AND ol.ordered_qty = 0
                  AND ol.allocated_qty = 0
                  AND ol.shipped_qty = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM order_line_substitutions later
                      WHERE later.original_line_id = ol.id AND later.reverted_at IS NULL
                  )
                """
            )
        ).fetchall()
    ]
    # alembic/env.py doesn't turn on SQLite's foreign_keys pragma, so the ON DELETE actions
    # the app relies on don't fire here — mirror them by hand for every FK onto order_lines.
    for line_id in ghost_ids:
        params = {"id": line_id}
        bind.execute(sa.text("UPDATE order_line_substitutions SET new_line_id = NULL WHERE new_line_id = :id"), params)
        bind.execute(sa.text("DELETE FROM order_line_substitutions WHERE original_line_id = :id"), params)
        bind.execute(sa.text("DELETE FROM allocation_events WHERE order_line_id = :id"), params)
        bind.execute(sa.text("DELETE FROM order_line_returns WHERE order_line_id = :id"), params)
        bind.execute(
            sa.text("UPDATE product_stock_events SET source_order_line_id = NULL WHERE source_order_line_id = :id"),
            params,
        )
        bind.execute(sa.text("DELETE FROM order_lines WHERE id = :id"), params)


def downgrade() -> None:
    """Downgrade schema."""
    # Rows whose line was deleted can't go back to NOT NULL; they have no line to point at.
    op.execute(sa.text("DELETE FROM order_line_substitutions WHERE new_line_id IS NULL"))
    with op.batch_alter_table('order_line_substitutions', naming_convention=_NAMING) as batch_op:
        batch_op.drop_constraint('fk_order_line_substitutions_new_line_id_order_lines', type_='foreignkey')
        batch_op.alter_column('new_line_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            'fk_order_line_substitutions_new_line_id_order_lines',
            'order_lines', ['new_line_id'], ['id'], ondelete='CASCADE',
        )
