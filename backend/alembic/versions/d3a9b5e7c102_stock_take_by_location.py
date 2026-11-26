"""stock takes can be scoped by location

A by-location take counts what each place inside a location holds: one line per item per place, so
the per-take uniqueness of a line now includes the place. See docs/plan-locations.md §7.1.

Revision ID: d3a9b5e7c102
Revises: c8f2a6d41b73
Create Date: 2026-11-25 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3a9b5e7c102'
down_revision: Union[str, Sequence[str], None] = 'c8f2a6d41b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("stock_takes") as batch:
        batch.add_column(sa.Column("location_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_stock_takes_location_id", "locations", ["location_id"], ["id"], ondelete="SET NULL")

    # The old unique constraint on (take, material) becomes an expression index that includes the
    # place; both existing indexes are rebuilt rather than altered.
    op.execute("DROP INDEX IF EXISTS uq_stock_take_lines_take_product_variant")
    with op.batch_alter_table("stock_take_lines") as batch:
        batch.drop_constraint("uq_stock_take_lines_take_material", type_="unique")
        batch.add_column(sa.Column("location_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("location_label", sa.String(), nullable=True))
        batch.add_column(sa.Column("found", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key("fk_stock_take_lines_location_id", "locations", ["location_id"], ["id"], ondelete="SET NULL")
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_take_lines_take_material ON stock_take_lines "
        "(stock_take_id, material_id, COALESCE(location_id, -1))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_take_lines_take_product_variant ON stock_take_lines "
        "(stock_take_id, product_id, COALESCE(variant_id, -1), COALESCE(location_id, -1))"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS uq_stock_take_lines_take_product_variant")
    op.execute("DROP INDEX IF EXISTS uq_stock_take_lines_take_material")
    # By-location lines can repeat an item, which the old constraints forbid.
    op.execute("DELETE FROM stock_take_lines WHERE location_id IS NOT NULL")
    with op.batch_alter_table("stock_take_lines") as batch:
        batch.drop_constraint("fk_stock_take_lines_location_id", type_="foreignkey")
        batch.drop_column("found")
        batch.drop_column("location_label")
        batch.drop_column("location_id")
        batch.create_unique_constraint("uq_stock_take_lines_take_material", ["stock_take_id", "material_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_take_lines_take_product_variant ON stock_take_lines "
        "(stock_take_id, product_id, COALESCE(variant_id, -1))"
    )
    with op.batch_alter_table("stock_takes") as batch:
        batch.drop_constraint("fk_stock_takes_location_id", type_="foreignkey")
        batch.drop_column("location_id")
