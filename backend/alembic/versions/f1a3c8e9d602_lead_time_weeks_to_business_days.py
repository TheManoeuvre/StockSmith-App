"""convert lead time fields from weeks to business days

Lead time (supplier-specific and shop-wide default) moves from a fractional number of
calendar weeks to a whole number of business days — the unit sellers actually quote it
in, and the unit the arrival-date estimate now steps forward in (skipping weekends).

Existing values are converted at 5 business days per week (weeks * 5, rounded to the
nearest whole day) so a shop's current forecast behaviour doesn't jump on upgrade.

Revision ID: f1a3c8e9d602
Revises: c7e2a9f3b104
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a3c8e9d602'
down_revision: Union[str, Sequence[str], None] = 'c7e2a9f3b104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('suppliers', sa.Column('default_lead_time_days', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE suppliers SET default_lead_time_days = CAST(ROUND(default_lead_time_weeks * 5) AS INTEGER) "
        "WHERE default_lead_time_weeks IS NOT NULL"
    )
    with op.batch_alter_table('suppliers') as batch:
        batch.drop_column('default_lead_time_weeks')

    op.add_column('general_settings', sa.Column('default_lead_time_days', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE general_settings SET default_lead_time_days = CAST(ROUND(default_lead_time_weeks * 5) AS INTEGER)"
    )
    with op.batch_alter_table('general_settings') as batch:
        batch.alter_column('default_lead_time_days', nullable=False, server_default='5')
        batch.drop_column('default_lead_time_weeks')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('general_settings', sa.Column('default_lead_time_weeks', sa.Numeric(6, 2), nullable=True))
    op.execute(
        "UPDATE general_settings SET default_lead_time_weeks = default_lead_time_days / 5.0"
    )
    with op.batch_alter_table('general_settings') as batch:
        batch.alter_column('default_lead_time_weeks', nullable=False, server_default='4')
        batch.drop_column('default_lead_time_days')

    op.add_column('suppliers', sa.Column('default_lead_time_weeks', sa.Numeric(6, 2), nullable=True))
    op.execute(
        "UPDATE suppliers SET default_lead_time_weeks = default_lead_time_days / 5.0 "
        "WHERE default_lead_time_days IS NOT NULL"
    )
    with op.batch_alter_table('suppliers') as batch:
        batch.drop_column('default_lead_time_days')
