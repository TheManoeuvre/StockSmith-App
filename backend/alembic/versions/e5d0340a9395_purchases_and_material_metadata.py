"""purchases and material metadata

Revision ID: e5d0340a9395
Revises: 2062e429185a
Create Date: 2026-07-07 14:20:33.211061

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5d0340a9395'
down_revision: Union[str, Sequence[str], None] = '2062e429185a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


purchase_status = sa.Enum("ordered", "received", name="purchase_status")


def upgrade() -> None:
    """Upgrade schema."""
    # New material metadata columns. colour/material_type are only meaningful for
    # category=filament in the UI, but nothing here enforces that at the DB level —
    # simple nullable columns, gated by the frontend form instead.
    op.add_column("materials", sa.Column("colour", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("material_type", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("barcode", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("manufacturer", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("product_url", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("image_path", sa.String(), nullable=True))
    op.add_column("materials", sa.Column("image_original_filename", sa.String(), nullable=True))

    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("supplier", sa.String(), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("status", purchase_status, nullable=False, server_default="ordered"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_purchases_status", "purchases", ["status"])

    # Clean-break restructure: only throwaway dev/verification data exists, so drop and
    # recreate rather than migrate the old flat shape. material_purchases becomes a line
    # item belonging to a purchases header; purchase_date/supplier move to the header.
    op.drop_table("material_purchases")
    op.create_table(
        "material_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "purchase_id", sa.Integer(), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0", name="ck_material_purchases_qty_positive"),
        sa.CheckConstraint("total_cost >= 0", name="ck_material_purchases_total_cost_nonneg"),
    )
    op.create_index("ix_material_purchases_purchase_id", "material_purchases", ["purchase_id"])
    op.create_index("ix_material_purchases_material_id", "material_purchases", ["material_id"])

    op.create_table(
        "material_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("qty_delta", sa.Numeric(14, 4), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty_delta != 0", name="ck_material_adjustments_qty_delta_nonzero"),
    )
    op.create_index("ix_material_adjustments_material_id", "material_adjustments", ["material_id"])

    op.execute(
        """
        CREATE TRIGGER trg_purchases_updated_at BEFORE UPDATE ON purchases
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_purchases_updated_at ON purchases")

    op.drop_table("material_adjustments")
    op.drop_table("material_purchases")
    op.drop_table("purchases")

    # Recreate the original flat material_purchases shape (matches initial migration).
    op.create_table(
        "material_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("purchase_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("supplier", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("qty > 0", name="ck_material_purchases_qty_positive"),
        sa.CheckConstraint("total_cost >= 0", name="ck_material_purchases_total_cost_nonneg"),
    )
    op.create_index("ix_material_purchases_material_id", "material_purchases", ["material_id"])
    op.create_index("ix_material_purchases_date", "material_purchases", ["purchase_date"])

    bind = op.get_bind()
    purchase_status.drop(bind, checkfirst=True)

    op.drop_column("materials", "image_original_filename")
    op.drop_column("materials", "image_path")
    op.drop_column("materials", "product_url")
    op.drop_column("materials", "manufacturer")
    op.drop_column("materials", "barcode")
    op.drop_column("materials", "material_type")
    op.drop_column("materials", "colour")
