"""add pushover expiry hours

Pushover's `ttl` is the only mechanism it offers for getting a delivered notification off a
phone: the message deletes itself from every device it reached once the TTL elapses. There
is no resolve-driven equivalent — tags/cancel_by_tag only halts emergency-priority retries,
and emergency priority re-alerts with sound until acknowledged, which no order shortfall
warrants.

This column is how long a *routine* notification lives on the phone, in hours, so a "short
by 1" alert for an order that shipped this morning ages off the lock screen. 0 disables it.
Blockers and failures never carry a TTL regardless — see services/notifications
._expiry_seconds.

Revision ID: e7c2a95d1b48
Revises: d4f8b1e63a07
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7c2a95d1b48'
down_revision: Union[str, Sequence[str], None] = 'd4f8b1e63a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_settings",
        sa.Column("pushover_expiry_hours", sa.Integer(), nullable=False, server_default="24"),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "pushover_expiry_hours")
