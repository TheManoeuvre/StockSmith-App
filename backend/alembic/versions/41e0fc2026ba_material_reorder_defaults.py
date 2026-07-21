"""material reorder defaults

Revision ID: 41e0fc2026ba
Revises: d53ac019fceb
Create Date: 2026-07-08 09:42:09.451057

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41e0fc2026ba'
down_revision: Union[str, Sequence[str], None] = 'd53ac019fceb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "materials",
        sa.Column("default_supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("materials", sa.Column("typical_reorder_qty", sa.Numeric(14, 4), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("materials", "typical_reorder_qty")
    op.drop_column("materials", "default_supplier_id")
