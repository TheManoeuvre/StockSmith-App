"""add work-flow stages, routes, route steps and progress events

The configurable steps between an order arriving and it shipping — see docs/plan-workflow.md.
Nothing is backfilled and the feature ships switched off (`general_settings.work_flow_enabled`),
so an existing install behaves exactly as before until its owner opts in. The built-in Make and
Ship stages and the default route are created by `seed.ensure_seed_data` on startup rather than
here, the same arrangement material units use: the test suite builds its schema with create_all
and never runs migrations, so the seed has to exist in code either way.

Revision ID: a4d7c19e8b25
Revises: e5b1c8d2a904
Create Date: 2026-11-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d7c19e8b25'
down_revision: Union[str, Sequence[str], None] = 'e5b1c8d2a904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "work_stages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("station", sa.String(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("custom", "make", "ship", name="work_stage_kind", native_enum=False, validate_strings=True),
            nullable=False,
        ),
        sa.Column("per_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("wait_minutes", sa.Integer(), nullable=True),
        sa.Column("typical_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint("wait_minutes IS NULL OR wait_minutes >= 0", name="ck_work_stages_wait_nonneg"),
        sa.CheckConstraint("typical_minutes IS NULL OR typical_minutes >= 0", name="ck_work_stages_typical_nonneg"),
    )

    op.create_table(
        "work_routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name"),
    )
    # Exactly one default route. A partial unique index rather than a CHECK: SQLite and Postgres
    # both support it, and "at most one row where is_default" is exactly what it expresses.
    op.execute("CREATE UNIQUE INDEX uq_work_routes_single_default ON work_routes (is_default) WHERE is_default")

    op.create_table(
        "work_route_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("route_id", sa.Integer(), sa.ForeignKey("work_routes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("work_stages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("mix_variants", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "qty_basis",
            sa.Enum("unit", "bom_category", name="work_qty_basis", native_enum=False, validate_strings=True),
            nullable=False,
            server_default="unit",
        ),
        sa.Column(
            "qty_category_id",
            sa.Integer(),
            sa.ForeignKey("material_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("route_id", "stage_id", name="uq_work_route_steps_route_stage"),
        sa.UniqueConstraint("route_id", "position", name="uq_work_route_steps_route_position"),
    )
    op.create_index("ix_work_route_steps_route_id", "work_route_steps", ["route_id"])

    op.create_table(
        "work_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "order_line_id", sa.Integer(), sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("stage_id", sa.Integer(), sa.ForeignKey("work_stages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("qty != 0", name="ck_work_events_qty_nonzero"),
    )
    op.create_index("ix_work_events_product_stage", "work_events", ["product_id", "stage_id"])
    op.create_index("ix_work_events_order_line_id", "work_events", ["order_line_id"])

    # A route on a product and on a product category. SQLite cannot add a column carrying a
    # foreign key in place, so batch mode rebuilds each table with the constraint declared.
    with op.batch_alter_table("products") as batch:
        batch.add_column(
            sa.Column(
                "work_route_id",
                sa.Integer(),
                sa.ForeignKey("work_routes.id", ondelete="SET NULL", name="fk_products_work_route_id"),
                nullable=True,
            )
        )
    with op.batch_alter_table("product_categories") as batch:
        batch.add_column(
            sa.Column(
                "work_route_id",
                sa.Integer(),
                sa.ForeignKey("work_routes.id", ondelete="SET NULL", name="fk_product_categories_work_route_id"),
                nullable=True,
            )
        )

    op.add_column(
        "general_settings",
        sa.Column("work_flow_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "general_settings",
        sa.Column("work_flow_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("general_settings", "work_flow_enabled_at")
    op.drop_column("general_settings", "work_flow_enabled")
    with op.batch_alter_table("product_categories") as batch:
        batch.drop_column("work_route_id")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("work_route_id")
    op.drop_table("work_events")
    op.drop_table("work_route_steps")
    op.drop_table("work_routes")
    op.drop_table("work_stages")
