"""add stock_take_lines.unmoved_since

A count sheet treats every line the same, but they are not the same. An item nothing has
touched since it was last counted — no adjustment, no delivery, no build, no order —
should already be sitting at the figure on the sheet, so checking it is a quick
confirmation rather than a real count. This column carries the date that item was last
counted, and is set only for lines where the ledgers show no movement since.

Nullable, written once when a take is created, and left NULL on every existing line: the
ledgers say what has happened since, but not what the answer would have been on the day a
past take was started, and back-filling a guess would put a low-risk badge on lines nobody
counted under that rule.

Revision ID: d4a2f7c91b63
Revises: b7e4d1c92a05
Create Date: 2026-09-20 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a2f7c91b63'
down_revision: Union[str, Sequence[str], None] = 'b7e4d1c92a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stock_take_lines",
        sa.Column("unmoved_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stock_take_lines", "unmoved_since")
