"""add replacement parcels and marketplace postage charges

Revision ID: c7d3a9e51b04
Revises: b2c6e4d18f75
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d3a9e51b04'
down_revision: Union[str, Sequence[str], None] = 'b2c6e4d18f75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('order_replacement_parcels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.Enum('faulty_item', 'missing_from_order', 'lost_in_transit', 'damaged_in_transit', 'other', 'unspecified', name='replacement_parcel_reason', native_enum=False), nullable=False),
    sa.Column('source', sa.Enum('manual', 'sync', name='replacement_parcel_source', native_enum=False), nullable=False),
    sa.Column('needs_review', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('postage_cost', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('tracking_number', sa.String(), nullable=True),
    sa.Column('carrier', sa.String(), nullable=True),
    sa.Column('notes', sa.String(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('order_replacement_parcel_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('parcel_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('variant_id', sa.Integer(), nullable=True),
    sa.Column('material_id', sa.Integer(), nullable=True),
    sa.Column('qty', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('unit_cost_snapshot', sa.Numeric(precision=14, scale=6), nullable=True),
    sa.CheckConstraint('qty > 0', name='ck_order_replacement_parcel_items_qty_positive'),
    sa.CheckConstraint('(product_id IS NOT NULL AND material_id IS NULL) OR (product_id IS NULL AND material_id IS NOT NULL)', name='ck_order_replacement_parcel_items_one_owner'),
    sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parcel_id'], ['order_replacement_parcels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('order_postage_charges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('platform', sa.Enum('etsy', 'ebay', name='listing_platform', native_enum=False), nullable=False),
    sa.Column('source', sa.Enum('ebay_shipping_label', 'etsy_ledger', name='postage_charge_source', native_enum=False), nullable=False),
    sa.Column('external_id', sa.String(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('currency', sa.String(), nullable=True),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('replacement_parcel_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['replacement_parcel_id'], ['order_replacement_parcels.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('platform', 'external_id', name='uq_order_postage_charges_platform_external_id'),
    )

    # Two new ProductStockEventType members, and the longer one (replacement_parcel_reversal,
    # 27 chars) outgrows the VARCHAR portable_enum sized to order_fulfillment — SQLite ignores
    # the length, Postgres would not (see models/base.portable_enum).
    with op.batch_alter_table('product_stock_events') as batch_op:
        batch_op.add_column(sa.Column('source_replacement_parcel_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_product_stock_events_source_replacement_parcel_id',
            'order_replacement_parcels',
            ['source_replacement_parcel_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.alter_column(
            'event_type',
            existing_type=sa.String(length=17),
            type_=sa.String(length=27),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('product_stock_events') as batch_op:
        batch_op.drop_constraint('fk_product_stock_events_source_replacement_parcel_id', type_='foreignkey')
        batch_op.drop_column('source_replacement_parcel_id')
        batch_op.alter_column(
            'event_type',
            existing_type=sa.String(length=27),
            type_=sa.String(length=17),
            existing_nullable=False,
        )

    op.drop_table('order_postage_charges')
    op.drop_table('order_replacement_parcel_items')
    op.drop_table('order_replacement_parcels')
