"""notification digest routing and schedule

Two related fixes to how notifications reach a phone.

1. notifications.delivery_mode. Deliveries were batched into the digest by `urgency`, a
   value fixed in code per alert condition, rather than by the user's per-type delivery
   setting. Any type whose setting was "immediate" but whose hard-coded urgency was
   "digest" therefore went out twice — once at dispatch, once again in the next digest
   sweep — and the mirror case (setting "digest", urgency "immediate") was never swept at
   all and so never left the in-app log. Recording the routing decision on the row makes
   the two axes independent: urgency now only gates quiet hours.

   Existing rows are backfilled from urgency, which is the closest thing to the routing
   decision that was actually made for them, and every not-yet-swept row is stamped as
   already sent. That stamp is deliberate: without it, the first flush after upgrading
   would blast out a digest of every historical digest-urgency notification the install has
   ever accumulated.

2. notification_settings.digest_hours_local / digest_last_fired_at. The digest flush ran on
   every scheduler tick, so a "digest" was just an immediate notification up to 15 minutes
   late, re-sent every 15 minutes while events kept trickling in. It now runs on configured
   local hours, defaulting to 9am and 5pm.

Revision ID: d4f8b1e63a07
Revises: a1c4e0f9d823
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4f8b1e63a07'
down_revision: Union[str, Sequence[str], None] = 'a1c4e0f9d823'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Matches the type the original notifications migration (fdc3ea941e39) declared for the
# identically-named column on notification_type_settings.
_NOTIFICATION_DELIVERY_MODE = sa.Enum(
    'immediate', 'digest', 'off', name='notification_delivery_mode', native_enum=False
)


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("delivery_mode", _NOTIFICATION_DELIVERY_MODE, nullable=False, server_default="immediate"),
    )
    # urgency is the only record these rows carry of how they were routed, so it is the
    # honest backfill; 'digest' urgency maps to 'digest' delivery, everything else to
    # 'immediate'.
    op.execute("UPDATE notifications SET delivery_mode = 'digest' WHERE urgency = 'digest'")
    # Close off the historical backlog so the first scheduled flush doesn't re-send it.
    op.execute("UPDATE notifications SET digest_sent_at = created_at WHERE digest_sent_at IS NULL")

    op.add_column(
        "notification_settings",
        sa.Column("digest_hours_local", sa.String(), nullable=False, server_default="9,17"),
    )
    op.add_column(
        "notification_settings",
        sa.Column("digest_last_fired_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "digest_last_fired_at")
    op.drop_column("notification_settings", "digest_hours_local")
    op.drop_column("notifications", "delivery_mode")
