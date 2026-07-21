"""backfill build material adjustment product/variant

Revision ID: d1a5b9e3f7c6
Revises: b8e4f1a7c3d2
Create Date: 2026-07-21 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd1a5b9e3f7c6'
down_revision: Union[str, Sequence[str], None] = 'b8e4f1a7c3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill product_id/variant_id on material_adjustments created by a build,
    for rows predating a seed-data gap where those columns were left null even
    though reason already encodes "Build #<id>". Without this, old build entries
    in a material's stock history render as bare text instead of a link to the
    product that consumed the material."""
    op.execute(
        """
        UPDATE material_adjustments ma
        SET product_id = b.product_id,
            variant_id = b.variant_id
        FROM builds b
        WHERE ma.product_id IS NULL
          AND ma.reason ~ '^Build #\\d+$'
          AND b.id = substring(ma.reason FROM '^Build #(\\d+)$')::int
        """
    )


def downgrade() -> None:
    """Backfilled values are indistinguishable from originally-set ones; no-op."""
    pass
