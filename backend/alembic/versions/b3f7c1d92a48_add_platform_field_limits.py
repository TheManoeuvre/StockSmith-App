"""add platform_field_limits override table

Marketplaces cap fields — SKU length, title length, how many variation attributes a listing
may have — and those caps change without warning on a shipped desktop build that has no way
to receive a code fix on the user's schedule. The numbers themselves live in code
(services/platform_limits.py) with a provenance comment recording how well each one is
actually known; this table exists so a wrong one can be corrected without a release.

Deliberately created EMPTY, and deliberately not seeded. That is the opposite of
platform_fee_components, and the difference matters: `_ensure_platform_fee_components`
returns early once any row exists, so an install that edited a single fee component never
receives corrected seed values again. For fee rates, which are the user's own numbers, that
is defensible. For a marketplace's field limits it would mean shipping a corrected SKU cap
that silently never reaches anyone whose table had been populated.

Storing only overrides inverts that. The code default always wins unless a human explicitly
overrode that one field, corrections ship normally with a release, and the settings UI can
honestly show "default 50 / overridden to 45" instead of presenting a stored number as
though it were the truth all along.

int_value and text_value are separate columns rather than one text column because the
numeric limits are compared, not merely displayed — the resolver picks the smallest across
the platforms a product targets, and a string comparison would rank "9" above "10".

The first real use is already known: Etsy's third-variation support reaches general
availability shortly, at which point variation_attribute_max_count moves from 2 to 3 for a
shop enrolled in it. That is a data change here rather than a code change, which is exactly
what this table is for.

Revision ID: b3f7c1d92a48
Revises: e6b21d84f309
Create Date: 2026-08-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f7c1d92a48'
down_revision: Union[str, Sequence[str], None] = 'e6b21d84f309'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'platform_field_limits',
        sa.Column('id', sa.Integer(), nullable=False),
        # VARCHAR + CHECK rather than a native enum, matching models/base.py's
        # portable_enum, so the schema is identical on SQLite and Postgres.
        sa.Column(
            'platform',
            sa.Enum('etsy', 'ebay', 'shopify', name='listing_platform', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'field_key',
            sa.Enum(
                'sku_max_length',
                'title_max_length',
                'title_charset',
                'description_max_length',
                'variation_attribute_max_count',
                'variation_max_count',
                'attribute_name_max_length',
                'attribute_value_max_length',
                'attribute_value_charset',
                'image_max_count',
                'price_decimal_places',
                'quantity_max',
                name='limit_field',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('int_value', sa.Integer(), nullable=True),
        sa.Column('text_value', sa.String(), nullable=True),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('platform', 'field_key', name='uq_platform_field_limits_platform_field'),
        sa.CheckConstraint(
            'int_value IS NOT NULL OR text_value IS NOT NULL',
            name='ck_platform_field_limits_has_value',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('platform_field_limits')
