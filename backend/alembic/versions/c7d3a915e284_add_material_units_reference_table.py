"""add material_units reference table

Promotes the unit of measure from a fixed three-value enum (g, ml, each) to editable rows, so a
unit such as metres can be added in Settings → Lists instead of in a Python enum, a TypeScript
union, two copied arrays, the display helpers and a migration.

The three behaviours that were keyed on a specific unit become columns, seeded here to exactly
what they were so this changes what units *are* without changing what the app does:

* "each" took whole numbers only            -> decimal_places = 0
* "g" and "ml" took any precision           -> decimal_places NULL
* "each" never printed its name beside a number -> show_suffix = 0

`materials.unit` and `material_categories.default_unit` keep holding the unit's *name* and are
deliberately not foreign keys (see app/models/material_unit.py). The only change to them is the
column type: the enum rendered as VARCHAR sized to its longest member ("each", 4), and a longer
unit name such as "metre" would overflow that on a dialect that enforces the length. SQLite
ignores it, so this is a no-op there beyond the table rebuild batch mode implies.

Revision ID: c7d3a915e284
Revises: b6e2d9f4a713
Create Date: 2026-11-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d3a915e284'
down_revision: Union[str, Sequence[str], None] = 'b6e2d9f4a713'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inlined rather than imported from app.services.units, which holds the same list: a migration
# records what happened at one revision, and one that imports app code silently re-runs against a
# future definition of it. name, sort_order, decimal_places, show_suffix.
_SEED = (
    ("g", 10, None, 1),
    ("ml", 20, None, 1),
    ("each", 30, 0, 0),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'material_units',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('decimal_places', sa.Integer(), nullable=True),
        sa.Column('show_suffix', sa.Boolean(), server_default=sa.text('1'), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    bind = op.get_bind()
    for name, sort_order, decimal_places, show_suffix in _SEED:
        bind.execute(
            sa.text(
                """
                INSERT INTO material_units (name, sort_order, decimal_places, show_suffix)
                VALUES (:name, :sort_order, :decimal_places, :show_suffix)
                """
            ),
            {"name": name, "sort_order": sort_order, "decimal_places": decimal_places, "show_suffix": show_suffix},
        )

    enum_type = sa.Enum('g', 'ml', 'each', name='material_unit', native_enum=False)
    with op.batch_alter_table('materials') as batch:
        batch.alter_column('unit', existing_type=enum_type, type_=sa.String(), existing_nullable=False)
    with op.batch_alter_table('material_categories') as batch:
        batch.alter_column('default_unit', existing_type=enum_type, type_=sa.String(), existing_nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # A unit added since can't be expressed by the old three-value enum, and the previous release
    # raises LookupError reading one. Move every such material back to a built-in before the
    # column is narrowed: grams is the closest thing to "a measured quantity" the old schema had.
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE materials SET unit = 'g' WHERE unit NOT IN ('g', 'ml', 'each')"))
    bind.execute(
        sa.text("UPDATE material_categories SET default_unit = NULL WHERE default_unit NOT IN ('g', 'ml', 'each')")
    )

    enum_type = sa.Enum('g', 'ml', 'each', name='material_unit', native_enum=False)
    with op.batch_alter_table('material_categories') as batch:
        batch.alter_column('default_unit', existing_type=sa.String(), type_=enum_type, existing_nullable=True)
    with op.batch_alter_table('materials') as batch:
        batch.alter_column('unit', existing_type=sa.String(), type_=enum_type, existing_nullable=False)
    op.drop_table('material_units')
