"""material types

Revision ID: b7c1e9f4a2d6
Revises: a3f9c7b12d84
Create Date: 2026-07-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1e9f4a2d6'
down_revision: Union[str, Sequence[str], None] = 'a3f9c7b12d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Normalizes materials.material_type (free text) into its own table, mirroring the
    manufacturers/suppliers migration — data-preserving, not a clean break.
    """
    op.create_table(
        "material_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.execute(
        """
        INSERT INTO material_types (name)
        SELECT DISTINCT material_type FROM materials
        WHERE material_type IS NOT NULL AND material_type <> ''
        """
    )

    op.add_column(
        "materials",
        sa.Column(
            "material_type_id", sa.Integer(), sa.ForeignKey("material_types.id", ondelete="SET NULL"), nullable=True
        ),
    )

    op.execute(
        """
        UPDATE materials m SET material_type_id = mt.id
        FROM material_types mt WHERE mt.name = m.material_type
        """
    )

    op.drop_column("materials", "material_type")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("materials", sa.Column("material_type", sa.String(), nullable=True))

    op.execute(
        """
        UPDATE materials m SET material_type = mt.name
        FROM material_types mt WHERE mt.id = m.material_type_id
        """
    )

    op.drop_column("materials", "material_type_id")
    op.drop_table("material_types")
