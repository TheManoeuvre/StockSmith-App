"""material adjustment mode

Revision ID: ab7b0c8a2ae7
Revises: b3f6d1a8c957
Create Date: 2026-07-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'ab7b0c8a2ae7'
down_revision: Union[str, Sequence[str], None] = 'b3f6d1a8c957'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

material_adjustment_mode = postgresql.ENUM("adjust", "set", name="material_adjustment_mode", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    material_adjustment_mode.create(op.get_bind())

    op.add_column(
        "material_adjustments",
        sa.Column("mode", material_adjustment_mode, nullable=False, server_default="adjust"),
    )
    op.add_column("material_adjustments", sa.Column("target_qty", sa.Numeric(14, 4), nullable=True))

    op.drop_constraint("ck_material_adjustments_qty_delta_nonzero", "material_adjustments", type_="check")
    op.create_check_constraint(
        "ck_material_adjustments_qty_delta_nonzero",
        "material_adjustments",
        "qty_delta != 0 OR mode = 'set'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_material_adjustments_qty_delta_nonzero", "material_adjustments", type_="check")
    op.create_check_constraint(
        "ck_material_adjustments_qty_delta_nonzero", "material_adjustments", "qty_delta != 0"
    )

    op.drop_column("material_adjustments", "target_qty")
    op.drop_column("material_adjustments", "mode")

    bind = op.get_bind()
    material_adjustment_mode.drop(bind, checkfirst=True)
