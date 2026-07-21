"""variant bom substitution

Revision ID: a3f9c7b12d84
Revises: e70d4b556d0e
Create Date: 2026-07-09 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f9c7b12d84'
down_revision: Union[str, Sequence[str], None] = 'e70d4b556d0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "product_variant_materials",
        sa.Column(
            "replaces_material_id",
            sa.Integer(),
            sa.ForeignKey("materials.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_product_variant_materials_no_self_substitution",
        "product_variant_materials",
        "replaces_material_id IS NULL OR replaces_material_id != material_id",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_product_variant_materials_no_self_substitution", "product_variant_materials", type_="check"
    )
    op.drop_column("product_variant_materials", "replaces_material_id")
