"""allow a variant BOM to use one material on more than one line

product_variant_materials was unique on (variant_id, material_id), which made it impossible
for a variant to resolve two base BOM lines onto the same material — a two-tone product
whose Primary and Accent colour attributes both pick "Apple Green" for one combination,
each part keeping its own quantity. Generation refused those combinations outright.

The uniqueness that actually matters is per (variant, material, base line replaced):
one row per base line landing on a material, with replaces_material_id NULL for the
product's own line on that material. NULLs are distinct under a plain UNIQUE, so the
constraint is expressed as a coalesced unique index, the same way uq_listings_product_
variant_platform is. Buildability now sums a variant's lines per material before working
out how many units the stock allows (see services/buildability).

Existing rows all satisfy the new index — it is strictly looser.

Revision ID: c3d7a9e14f52
Revises: b2c6e4d18f75
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d7a9e14f52'
down_revision: Union[str, Sequence[str], None] = 'b2c6e4d18f75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch mode: SQLite can't drop a UNIQUE constraint in place, so this rebuilds the table.
    with op.batch_alter_table('product_variant_materials', schema=None) as batch_op:
        batch_op.drop_constraint('uq_product_variant_materials_variant_material', type_='unique')
    op.create_index(
        'uq_product_variant_materials_variant_material_line',
        'product_variant_materials',
        ['variant_id', 'material_id', sa.text('coalesce(replaces_material_id, -1)')],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_product_variant_materials_variant_material_line', table_name='product_variant_materials')
    # Fails if any variant now has two lines on one material — those rows have to be merged
    # by hand before downgrading, since neither can be dropped without changing the BOM.
    with op.batch_alter_table('product_variant_materials', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_product_variant_materials_variant_material', ['variant_id', 'material_id']
        )
