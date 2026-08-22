"""add etsy_readiness_state_id to listing profiles

Etsy's createDraftListing docs list readiness_state_id as optional, but the live endpoint
refuses a physical draft without one ("A readiness_state_id is required for physical
listings"). That was a real gap: draft_readiness never checked for it, so the app told
sellers a listing was ready to draft right up until Etsy's 400. Same shape as the other
Etsy profile fields — a plain nullable column, nothing seeded or backfilled.

Revision ID: d1a4f8c62b95
Revises: c4f0e9a72b18
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1a4f8c62b95'
down_revision: Union[str, Sequence[str], None] = 'c4f0e9a72b18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('listing_profiles', sa.Column('etsy_readiness_state_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('listing_profiles') as batch:
        batch.drop_column('etsy_readiness_state_id')
