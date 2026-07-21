"""manufacturers and suppliers

Revision ID: ef7aeb026c43
Revises: 464ccf61382a
Create Date: 2026-07-08 09:05:15.827809

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef7aeb026c43'
down_revision: Union[str, Sequence[str], None] = '464ccf61382a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Data-preserving (unlike earlier clean-break migrations): existing free-text
    materials.manufacturer / purchases.supplier values belong to a real, actively-used
    app now, so they're migrated into the new normalized tables rather than dropped.
    """
    op.create_table(
        "manufacturers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("website_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("website_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Seed the new tables from existing distinct non-null string values.
    op.execute(
        """
        INSERT INTO manufacturers (name)
        SELECT DISTINCT manufacturer FROM materials
        WHERE manufacturer IS NOT NULL AND manufacturer <> ''
        """
    )
    op.execute(
        """
        INSERT INTO suppliers (name)
        SELECT DISTINCT supplier FROM purchases
        WHERE supplier IS NOT NULL AND supplier <> ''
        """
    )

    op.add_column(
        "materials",
        sa.Column("manufacturer_id", sa.Integer(), sa.ForeignKey("manufacturers.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "purchases",
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
    )

    # Backfill FKs by matching name.
    op.execute(
        """
        UPDATE materials m SET manufacturer_id = mf.id
        FROM manufacturers mf WHERE mf.name = m.manufacturer
        """
    )
    op.execute(
        """
        UPDATE purchases p SET supplier_id = s.id
        FROM suppliers s WHERE s.name = p.supplier
        """
    )

    op.drop_column("materials", "manufacturer")
    op.drop_column("purchases", "supplier")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("materials", sa.Column("manufacturer", sa.String(), nullable=True))
    op.add_column("purchases", sa.Column("supplier", sa.String(), nullable=True))

    op.execute(
        """
        UPDATE materials m SET manufacturer = mf.name
        FROM manufacturers mf WHERE mf.id = m.manufacturer_id
        """
    )
    op.execute(
        """
        UPDATE purchases p SET supplier = s.name
        FROM suppliers s WHERE s.id = p.supplier_id
        """
    )

    op.drop_column("materials", "manufacturer_id")
    op.drop_column("purchases", "supplier_id")

    op.drop_table("suppliers")
    op.drop_table("manufacturers")
