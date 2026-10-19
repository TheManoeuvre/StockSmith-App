"""add etsy_auto_renew and ebay_handling_time_days to listing_profiles

Etsy's should_auto_renew and an eBay handling time in business days, both nullable so an
existing profile keeps sending exactly what it sent before (nothing for these fields).

Revision ID: d4e8b2a6c917
Revises: a7c2e4b19d35
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e8b2a6c917'
down_revision: Union[str, Sequence[str], None] = 'a7c2e4b19d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('listing_profiles') as batch_op:
        batch_op.add_column(sa.Column('etsy_auto_renew', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('ebay_handling_time_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('listing_profiles') as batch_op:
        batch_op.drop_column('ebay_handling_time_days')
        batch_op.drop_column('etsy_auto_renew')
