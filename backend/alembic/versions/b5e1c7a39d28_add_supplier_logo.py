"""add supplier logo columns

A supplier can carry a logo, shown on purchases: a favicon fetched from its website, or an
uploaded image that overrides it. Existing suppliers start with none.

Revision ID: b5e1c7a39d28
Revises: a4d91c7e2b35
Create Date: 2026-11-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e1c7a39d28'
down_revision: Union[str, Sequence[str], None] = 'a4d91c7e2b35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("suppliers") as batch:
        batch.add_column(sa.Column("logo_path", sa.String(), nullable=True))
        batch.add_column(sa.Column("logo_source", sa.String(), nullable=True))
        batch.add_column(sa.Column("logo_updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("logo_updated_at")
        batch.drop_column("logo_source")
        batch.drop_column("logo_path")
