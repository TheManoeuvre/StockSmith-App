"""add product types, ABC classification, and per-item stock-take tracking

Groundwork for stock takes. Cycle counting needs to know which items are worth counting
often and which are not, and nothing in StockSmith expressed that — so this adds the
classification, the cadence it drives, and the per-item "when was this last counted"
field the overdue list reads.

Three levels resolve to an item's tier, most specific first: the item's own abc_class,
then its category (materials) or type (products), then a shop-wide baseline. All three
are nullable/sparse, which is what makes "inherit" distinguishable from "explicitly set
to C" — the same reasoning that keeps product_variant_materials a separate override
table rather than nullable columns.

product_types is new because products had no grouping axis at all: no category enum, no
lookup table, only name/SKU/description. Materials key their middle level on the
`category` enum instead of material_types, because the UI only populates
material_type_id for filament, so hardware, packaging and blanks would all have fallen
through to the baseline and the level would have done nothing for them.

abc_tier_settings ships EMPTY on purpose. The 30/60/90-day cadences live as code defaults
in services/abc.py and a row here only exists once someone overrides one — the sparse
shape platform_field_limits already uses. Seeding rows instead would fork every install's
values from the defaults at migration time, so a later improvement to those numbers would
reach nobody.

The two general_settings baselines default to 'C', the count-least tier. Switching this
feature on should not declare the entire catalogue due for counting every 30 days.

last_stock_take_id is deliberately not added here — stock_takes does not exist yet. It
arrives with that table in the following migration, alongside the lifecycle that sets it.

Revision ID: a7c3e1f09b52
Revises: c9d2e4a7f610
Create Date: 2026-08-15 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e1f09b52'
down_revision: Union[str, Sequence[str], None] = 'c9d2e4a7f610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ABC_CLASS = sa.Enum('A', 'B', 'C', name='abc_class', native_enum=False)
_ABC_SCOPE = sa.Enum('material', 'product', name='abc_scope', native_enum=False)
_MATERIAL_CATEGORY = sa.Enum(
    'filament', 'resin', 'pigment', 'hardware', 'packaging', 'blanks', 'other',
    name='material_category',
    native_enum=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'product_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    # SET NULL rather than RESTRICT: deleting a type should not be blocked by, or cascade
    # into, the products carrying it. services/reference_data.py refuses the delete while
    # anything references it anyway — this is the backstop for rows removed by other means.
    #
    # batch_alter_table because this add carries a constraint. SQLite has no ADD
    # CONSTRAINT, and Alembic issues the foreign key as a separate operation even when it
    # is declared inline on the column — so a plain op.add_column raises here regardless of
    # how the FK is written. Batch mode's copy-and-move rebuilds products with the
    # constraint in place; its existing named CHECK constraints and the shipping_profiles
    # FK are reflected and carried over (asserted in test_abc_classification.py).
    with op.batch_alter_table('products') as batch:
        batch.add_column(
            sa.Column(
                'product_type_id',
                sa.Integer(),
                sa.ForeignKey('product_types.id', ondelete='SET NULL', name='fk_products_product_type_id'),
                nullable=True,
            )
        )

    # Tier assignment for a whole category / product type. Sparse — a row exists only
    # where a tier was actually chosen.
    op.create_table(
        'material_category_abc',
        sa.Column('category', _MATERIAL_CATEGORY, nullable=False),
        sa.Column('abc_class', _ABC_CLASS, nullable=False),
        sa.PrimaryKeyConstraint('category'),
    )
    op.create_table(
        'product_type_abc',
        sa.Column('product_type_id', sa.Integer(), nullable=False),
        sa.Column('abc_class', _ABC_CLASS, nullable=False),
        sa.ForeignKeyConstraint(['product_type_id'], ['product_types.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('product_type_id'),
    )

    op.create_table(
        'abc_tier_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope', _ABC_SCOPE, nullable=False),
        sa.Column('tier', _ABC_CLASS, nullable=False),
        sa.Column('interval_days', sa.Integer(), nullable=False),
        sa.CheckConstraint('interval_days > 0', name='ck_abc_tier_settings_interval_positive'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope', 'tier', name='uq_abc_tier_settings_scope_tier'),
    )

    # Per-item overrides and count tracking. All nullable: NULL means "inherit" for the
    # first two and "never counted" for the third.
    for table in ('materials', 'products'):
        op.add_column(table, sa.Column('abc_class', _ABC_CLASS, nullable=True))
        op.add_column(table, sa.Column('stock_take_interval_days', sa.Integer(), nullable=True))
    for table in ('materials', 'products', 'product_variants'):
        op.add_column(table, sa.Column('last_stock_take_at', sa.DateTime(timezone=True), nullable=True))

    # server_default so the existing single settings row backfills to the same value a new
    # row would get from the model's Python-side default.
    op.add_column(
        'general_settings',
        sa.Column('default_material_abc_class', _ABC_CLASS, server_default='C', nullable=False),
    )
    op.add_column(
        'general_settings',
        sa.Column('default_product_abc_class', _ABC_CLASS, server_default='C', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # batch_alter_table throughout: dropping a column that carries a constraint needs a
    # table rebuild on SQLite, and grouping each table's drops into one batch rebuilds it
    # once rather than once per column.
    with op.batch_alter_table('general_settings') as batch:
        batch.drop_column('default_product_abc_class')
        batch.drop_column('default_material_abc_class')
    with op.batch_alter_table('product_variants') as batch:
        batch.drop_column('last_stock_take_at')
    for table in ('products', 'materials'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('last_stock_take_at')
            batch.drop_column('stock_take_interval_days')
            batch.drop_column('abc_class')
    op.drop_table('abc_tier_settings')
    op.drop_table('product_type_abc')
    op.drop_table('material_category_abc')
    with op.batch_alter_table('products') as batch:
        batch.drop_column('product_type_id')
    op.drop_table('product_types')
