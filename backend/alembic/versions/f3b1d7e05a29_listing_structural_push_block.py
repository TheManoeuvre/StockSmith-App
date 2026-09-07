"""listing structural push block marker

Stage 1 of the remaining listing-push rate-reduction work (the Stage 4 rider in
docs/plan-listing-push-rate-reduction.md, and the docs/backlog.md entry "Etsy quantity
pushes fail permanently when a listing's quantity doesn't vary by variation").

Adds listings.structural_push_block / structural_push_block_at — non-NULL when a per-SKU
quantity push can never succeed with the listing configured as it is on the marketplace
(the Etsy "quantity must be consistent across all products" case, detected from the GET
push_listing_quantity already performs). While set, the fan-out and the reconcile sweep's
normal selection skip the listing and the menu-bar badge leaves it out, so a structural
misconfiguration points the user at the listing to fix rather than accruing a retry count
that can never clear.

Additive and nullable on existing rows; no backfill.

Revision ID: f3b1d7e05a29
Revises: c9d1f4a7b230
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3b1d7e05a29'
down_revision: Union[str, Sequence[str], None] = 'c9d1f4a7b230'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('listings', sa.Column('structural_push_block', sa.String(), nullable=True))
    op.add_column('listings', sa.Column('structural_push_block_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('listings') as batch:
        batch.drop_column('structural_push_block_at')
        batch.drop_column('structural_push_block')
