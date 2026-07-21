"""platform ceiling qty

Revision ID: 17fcb4346ec9
Revises: ae97d79165de
Create Date: 2026-07-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '17fcb4346ec9'
down_revision: Union[str, Sequence[str], None] = 'ae97d79165de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("platform_ceiling_qty", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_products_platform_ceiling_qty_nonneg", "products", "platform_ceiling_qty IS NULL OR platform_ceiling_qty >= 0"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_products_platform_ceiling_qty_nonneg", "products", type_="check")
    op.drop_column("products", "platform_ceiling_qty")
