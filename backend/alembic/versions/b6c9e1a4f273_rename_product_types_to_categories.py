"""rename product types to product categories

Materials have had a category since the beginning; products had no grouping axis at all
until the stock-take work added one and called it a "type". Same job, same place in the
UI, two different words — and the two list pages are meant to read alike, which they
cannot while one groups by category and the other by type.

This is a rename and nothing else: no column gains or loses meaning, and no row moves.

It exists as its own revision rather than being folded back into a7c3e1f09b52 because
that revision has already run against real databases — the packaged build shipped it —
so rewriting it in place would rename nothing for anyone already carrying the tables,
while silently disagreeing with the code about what they are called.

The foreign key constraint on products keeps its old name (fk_products_product_type_id).
Nothing declares or reads it: the models name neither FK, and correcting it would mean
rebuilding the products table — a copy-and-move of the largest table here, carrying every
CHECK constraint across, to fix a string no query mentions.

Revision ID: b6c9e1a4f273
Revises: c1f7b3e08d42
Create Date: 2026-08-19 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6c9e1a4f273'
down_revision: Union[str, Sequence[str], None] = 'c1f7b3e08d42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Both dialects update the FK clauses in referencing tables as part of RENAME TO, so
    # products and product_type_abc keep pointing at the right place without being touched.
    op.rename_table('product_types', 'product_categories')
    op.rename_table('product_type_abc', 'product_category_abc')

    # A bare column rename, which SQLite has done natively since 3.25 and Postgres always
    # has — deliberately not batch_alter_table, which would rebuild products wholesale.
    op.alter_column('products', 'product_type_id', new_column_name='product_category_id')
    op.alter_column('product_category_abc', 'product_type_id', new_column_name='product_category_id')


def downgrade() -> None:
    op.alter_column('product_category_abc', 'product_category_id', new_column_name='product_type_id')
    op.alter_column('products', 'product_category_id', new_column_name='product_type_id')
    op.rename_table('product_category_abc', 'product_type_abc')
    op.rename_table('product_categories', 'product_types')
