"""normalize each unit for hardware packaging blanks

Revision ID: 50380e3d75dc
Revises: 170896b4ae84
Create Date: 2026-07-26 21:48:54.019592

"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50380e3d75dc'
down_revision: Union[str, Sequence[str], None] = '170896b4ae84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _round(value) -> Decimal:
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def upgrade() -> None:
    """Normalize Hardware/Packaging/Blanks materials to unit='each', rounding any
    existing fractional quantities to whole numbers in the process.

    current_qty is a derived/cached column (see costing.recompute_material) rebuilt
    from purchase + adjustment history, so it can't just be overwritten here — a
    compensating MaterialAdjustment audit row is inserted alongside it so any future
    recompute_material() replay lands on the same rounded value. allocated_qty/
    reorder_threshold/typical_reorder_qty aren't replay-derived, so those are updated
    directly. Round-half-up is applied identically to current_qty and allocated_qty,
    which is monotonic non-decreasing and therefore can't flip their relative order —
    so the allocated_qty <= current_qty check constraint can't be violated.
    """
    conn = op.get_bind()
    materials = sa.table(
        "materials",
        sa.column("id", sa.Integer),
        sa.column("category", sa.String),
        sa.column("unit", sa.String),
        sa.column("current_qty", sa.Numeric(14, 4)),
        sa.column("allocated_qty", sa.Numeric(14, 4)),
        sa.column("reorder_threshold", sa.Numeric(14, 4)),
        sa.column("typical_reorder_qty", sa.Numeric(14, 4)),
    )
    material_adjustments = sa.table(
        "material_adjustments",
        sa.column("material_id", sa.Integer),
        sa.column("mode", sa.String),
        sa.column("qty_delta", sa.Numeric(14, 4)),
        sa.column("target_qty", sa.Numeric(14, 4)),
        sa.column("reason", sa.String),
    )

    rows = conn.execute(
        sa.select(
            materials.c.id,
            materials.c.current_qty,
            materials.c.allocated_qty,
            materials.c.reorder_threshold,
            materials.c.typical_reorder_qty,
        ).where(
            materials.c.category.in_(("hardware", "packaging", "blanks")),
            materials.c.unit != "each",
        )
    ).fetchall()

    for row in rows:
        rounded_current = _round(row.current_qty)
        rounded_allocated = _round(row.allocated_qty)
        rounded_threshold = _round(row.reorder_threshold)
        rounded_typical = _round(row.typical_reorder_qty) if row.typical_reorder_qty is not None else None

        conn.execute(
            materials.update()
            .where(materials.c.id == row.id)
            .values(
                unit="each",
                current_qty=rounded_current,
                allocated_qty=rounded_allocated,
                reorder_threshold=rounded_threshold,
                typical_reorder_qty=rounded_typical,
            )
        )

        delta = rounded_current - Decimal(row.current_qty)
        if delta != 0:
            conn.execute(
                material_adjustments.insert().values(
                    material_id=row.id,
                    mode="adjust",
                    qty_delta=delta,
                    target_qty=None,
                    reason="System: rounded to a whole number when normalizing unit to 'each'",
                )
            )


def downgrade() -> None:
    """Irreversible data migration: the original units and fractional quantities for
    the affected rows are not recoverable. No-op — restore from a backup taken before
    this migration if you need to revert."""
    pass
