"""add material_categories reference table and backfill materials.category_id

Promotes materials.category from a fixed seven-value enum to a real reference row, so a category
can be added, renamed or reordered in Settings instead of in a Python enum, a TypeScript union,
two copied arrays, a label map and a migration rebuilding a CHECK constraint.

The reason category was the last lookup to make this move is that it was never only a label.
Four behaviours were keyed on a specific value — filament is still consumed by a failed build,
packaging is auto-added as a per-order kitting override, filament shows Colour and Material type
fields, filament's average cost reads per kilo — and a fifth, the default unit, was keyed on a
set of three. Those become columns on the row, seeded here to exactly the behaviour they had, so
this migration changes what categories *are* without changing what the app *does*.

`materials.category` is deliberately NOT dropped. Same reasoning as the colours migration
(e6b21d84f309): SQLite needs a full table rebuild to drop a column, and restoring an older backup
and migrating it forward is a routine operation, so one-way changes wait until a release has
passed with nothing depending on them.

That deferral has a visible cost worth stating plainly. The legacy column is NOT NULL and its
CHECK constraint accepts exactly the original seven strings, so a material filed under a
user-created category has to store 'other' there. On this release that column is written but
never read, so nothing is wrong; on the *previous* release — or in a backup restored into it —
such a material reads back as "other". Dropping the column next release is what settles it.

Revision ID: f2a91c4d7b08
Revises: d4e8b1c73f92
Create Date: 2026-08-17 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a91c4d7b08'
down_revision: Union[str, Sequence[str], None] = 'd4e8b1c73f92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inlined rather than imported from app.services.material_categories, which holds the same list.
# A migration records what happened at one revision; one that imports app code silently re-runs
# against whatever that code says later, which is how frozen migrations rot.
#
# Order is the original materials-page order — roughly by how often each comes up — which is why
# alphabetical would have been a visible regression. Gaps of 10 leave room to insert by hand.
_SEED = (
    # name, sort_order, default_unit, failed-build, kitting, colour, material type, cost/kg
    ("filament", 10, "g", 1, 0, 1, 1, 1),
    ("resin", 20, "g", 0, 0, 0, 0, 0),
    ("pigment", 30, "g", 0, 0, 0, 0, 0),
    ("hardware", 40, "each", 0, 0, 0, 0, 0),
    ("packaging", 50, "each", 0, 1, 0, 0, 0),
    ("blanks", 60, "each", 0, 0, 0, 0, 0),
    ("other", 70, "g", 0, 0, 0, 0, 0),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'material_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column(
            'default_unit',
            sa.Enum('g', 'ml', 'each', name='material_unit', native_enum=False),
            nullable=True,
        ),
        sa.Column('consumed_on_failed_build', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('auto_kitting_per_order', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('tracks_colour', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('tracks_material_type', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('cost_per_kg_display', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    bind = op.get_bind()
    for name, sort_order, unit, failed, kitting, colour, mtype, per_kg in _SEED:
        bind.execute(
            sa.text(
                """
                INSERT INTO material_categories
                    (name, sort_order, default_unit, consumed_on_failed_build,
                     auto_kitting_per_order, tracks_colour, tracks_material_type,
                     cost_per_kg_display)
                VALUES (:name, :sort_order, :unit, :failed, :kitting, :colour, :mtype, :per_kg)
                """
            ),
            {
                "name": name,
                "sort_order": sort_order,
                "unit": unit,
                "failed": failed,
                "kitting": kitting,
                "colour": colour,
                "mtype": mtype,
                "per_kg": per_kg,
            },
        )

    # Batch mode, because SQLite has no ALTER TABLE ADD CONSTRAINT — Alembic emits one for the
    # foreign key however the column is declared, and only the copy-and-move strategy can carry
    # it. The FK has to be real: ON DELETE RESTRICT is what stops a category being deleted out
    # from under the materials using it, and app/db.py turns enforcement on for every connection.
    #
    # RESTRICT rather than the SET NULL the other reference FKs use. Those can afford to blank a
    # manufacturer; a material with no category is not a state the app models.
    with op.batch_alter_table('materials') as batch:
        batch.add_column(sa.Column('category_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_materials_category_id', 'material_categories', ['category_id'], ['id'], ondelete='RESTRICT'
        )

    # An exact match, not a case-insensitive fold like the colours backfill needed. Colour was
    # free text that had accumulated spelling variants; category was an enum whose CHECK
    # constraint guarantees every row holds one of the seven strings seeded above. So this
    # resolves every material, and the test asserts it does.
    bind.execute(
        sa.text(
            """
            UPDATE materials
               SET category_id = (
                   SELECT c.id FROM material_categories c WHERE c.name = materials.category
               )
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # materials.category was never touched, so every category is still readable from it and
    # nothing needs recovering. Batch mode because dropping a column that carries a constraint
    # means a table rebuild on SQLite.
    with op.batch_alter_table('materials') as batch:
        batch.drop_constraint('fk_materials_category_id', type_='foreignkey')
        batch.drop_column('category_id')
    op.drop_table('material_categories')
