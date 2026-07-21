"""platform sync runs

Revision ID: e5f8b3d1c7a4
Revises: d3e7a1c9f5b8
Create Date: 2026-07-10 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e5f8b3d1c7a4'
down_revision: Union[str, Sequence[str], None] = 'd3e7a1c9f5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sync_run_mode = postgresql.ENUM("preview", "commit", name="sync_run_mode", create_type=False)
sync_run_status = postgresql.ENUM("success", "error", name="sync_run_status", create_type=False)
listing_platform = postgresql.ENUM("etsy", "ebay", "shopify", name="listing_platform", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    sync_run_mode.create(op.get_bind())
    sync_run_status.create(op.get_bind())

    op.create_table(
        "platform_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", listing_platform, nullable=False),
        sa.Column("mode", sync_run_mode, nullable=False),
        sa.Column("status", sync_run_status, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_mapping_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("platform_sync_runs")

    sync_run_status.drop(op.get_bind())
    sync_run_mode.drop(op.get_bind())
