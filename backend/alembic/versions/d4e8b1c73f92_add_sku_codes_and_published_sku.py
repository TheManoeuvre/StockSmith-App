"""add attribute value code registry and listings.published_sku

Two changes serving one rule: a SKU that a marketplace is already using must never change.

`product_attribute_value_codes` lets a generated SKU be short by construction. The readable
scheme joins slugified attribute values, so its length depends on how long those values
happen to be — "SKU-0037-6-STUD-SUNFLOWER-YELLOW" is 32 characters, exactly Etsy's cap,
with nothing spare for a third attribute or a longer colour name. Substituting a short
allocated code per value makes length a function of how many attributes a product has
instead: "SKU-0037-02-01-05" is 17 and stays there.

The codes are stored rather than derived from list position, and that is the point of the
table rather than an implementation detail. A positional code renumbers when a value is
deleted, which would silently rewrite the SKU of variants already published — the exact
thing this is meant to prevent. Codes are allocated on first use and never reused, because
a retired code may still be printed on a listing that exists.

`listings.published_sku` records what a marketplace actually acknowledged, which was not
answerable before: `external_listing_id` holds the listing id on Etsy and the SKU on eBay,
and re-deriving the SKU asks the wrong question because the derived value is the thing
about to change.

The eBay rows are backfilled from `external_listing_id` because there it *is* the SKU —
that is the documented overloading. Etsy rows are deliberately left null: there the column
holds a listing id, and copying it would populate the field with confident nonsense. An
Etsy row fills in on its next successful sync check instead.

Revision ID: d4e8b1c73f92
Revises: c9d2e4a7f610
Create Date: 2026-08-14 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e8b1c73f92'
down_revision: Union[str, Sequence[str], None] = 'c9d2e4a7f610'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'product_attribute_value_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('attribute_slot', sa.Integer(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('code', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('product_id', 'attribute_slot', 'value', name='uq_attribute_value_codes_value'),
        # One code per slot per product: without this two values could be handed the same
        # code and the SKUs built from them would collide.
        sa.UniqueConstraint('product_id', 'attribute_slot', 'code', name='uq_attribute_value_codes_code'),
    )

    # A plain column add, so no batch_alter_table — that is only needed on SQLite for
    # constraint changes and column drops.
    op.add_column('listings', sa.Column('published_sku', sa.String(), nullable=True))

    # On eBay external_listing_id holds the SKU itself, so this is a free and correct
    # backfill. On Etsy it holds a listing id; copying that would fill the column with a
    # number that looks like an answer and isn't.
    op.execute(
        """
        UPDATE listings
           SET published_sku = external_listing_id
         WHERE platform = 'ebay'
           AND external_listing_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('listings') as batch:
        batch.drop_column('published_sku')
    op.drop_table('product_attribute_value_codes')
