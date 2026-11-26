"""add storage locations, stock placements and placement events

Locations are an optional, free-form tree; placements record how much of a material or product
is in each. Nothing is backfilled: every existing item starts unplaced. See
docs/plan-locations.md.

Revision ID: c8f2a6d41b73
Revises: b5e1c7a39d28
Create Date: 2026-11-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8f2a6d41b73'
down_revision: Union[str, Sequence[str], None] = 'b5e1c7a39d28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_locations_parent_id", "locations", ["parent_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_locations_sibling_name ON locations (parent_id, lower(name)) "
        "WHERE parent_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_locations_top_level_name ON locations (lower(name)) WHERE parent_id IS NULL"
    )

    op.create_table(
        "stock_placements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("qty >= 0", name="ck_stock_placements_qty_nonneg"),
        sa.CheckConstraint(
            "(material_id IS NOT NULL AND product_id IS NULL AND variant_id IS NULL)"
            " OR (material_id IS NULL AND product_id IS NOT NULL)",
            name="ck_stock_placements_one_owner",
        ),
        sa.UniqueConstraint("location_id", "material_id", name="uq_stock_placements_location_material"),
        sa.UniqueConstraint("location_id", "product_id", "variant_id", name="uq_stock_placements_location_variant"),
    )
    op.create_index("ix_stock_placements_location_id", "stock_placements", ["location_id"])
    op.create_index("ix_stock_placements_material_id", "stock_placements", ["material_id"])
    op.create_index("ix_stock_placements_product_id", "stock_placements", ["product_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_placements_location_product ON stock_placements (location_id, product_id) "
        "WHERE variant_id IS NULL AND product_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_placements_primary_material ON stock_placements (material_id) "
        "WHERE is_primary AND material_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_stock_placements_primary_product ON stock_placements "
        "(product_id, coalesce(variant_id, 0)) WHERE is_primary AND product_id IS NOT NULL"
    )

    op.create_table(
        "placement_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=True),
        sa.Column(
            "variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("from_location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_location_id", sa.Integer(), sa.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "place", "move", "remove", "auto_deduct", "auto_add", "count",
                name="placement_event_kind", native_enum=False, validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_placement_events_material_id", "placement_events", ["material_id"])
    op.create_index("ix_placement_events_product_id", "placement_events", ["product_id"])
    op.create_index("ix_placement_events_from_location_id", "placement_events", ["from_location_id"])
    op.create_index("ix_placement_events_to_location_id", "placement_events", ["to_location_id"])
    op.create_index("ix_placement_events_created_at", "placement_events", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("placement_events")
    op.drop_table("stock_placements")
    op.drop_table("locations")
