"""add colours reference table and backfill materials.colour_id

Promotes materials.colour from free text to a real reference row, so a colour can be renamed
once instead of edited on every material that mentions it — the same property manufacturer,
supplier and material type already had.

The backfill groups case-insensitively, because free text accumulates "Black", "black" and
" BLACK " as three distinct values that mean one thing. The surviving spelling is the most
frequently used one, which is the closest thing to an intended canonical form that the data
actually contains; ties fall back to alphabetical so the result doesn't depend on row order.

Values that look like hex codes get one populated. The field is labelled "Colour / hex" in the
UI, so a real share of them are literally "#FF00AA". The original text stays as the name rather
than trying to invent one.

`materials.colour` is deliberately NOT dropped here. SQLite needs a full table rebuild to drop a
column, and now that restoring an older backup and migrating it forward is a routine operation,
one-way changes want to wait until a release has passed with nothing writing to it.

Revision ID: e6b21d84f309
Revises: d8a3f0c51e27
Create Date: 2026-08-09 14:20:00.000000

"""
import re
from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6b21d84f309'
down_revision: Union[str, Sequence[str], None] = 'd8a3f0c51e27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'colours',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('hex_code', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    # Batch mode, because SQLite has no ALTER TABLE ADD CONSTRAINT — Alembic emits one for the
    # foreign key however the column is declared, and only the copy-and-move strategy can carry
    # it. The FK has to be real rather than skipped: ON DELETE SET NULL is what stops a deleted
    # colour leaving dangling ids behind, and app/db.py turns enforcement on for every connection.
    with op.batch_alter_table('materials') as batch:
        batch.add_column(sa.Column('colour_id', sa.Integer(), nullable=True))
        batch.create_foreign_key(
            'fk_materials_colour_id', 'colours', ['colour_id'], ['id'], ondelete='SET NULL'
        )

    bind = op.get_bind()

    rows = bind.execute(
        sa.text("SELECT colour FROM materials WHERE colour IS NOT NULL AND TRIM(colour) != ''")
    ).fetchall()

    # Group case-insensitively; count how often each exact spelling appears so the most common
    # one can win.
    spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (raw,) in rows:
        trimmed = raw.strip()
        if trimmed:
            spellings[trimmed.casefold()][trimmed] += 1

    canonical: dict[str, str] = {}
    for folded, counts in spellings.items():
        # Most frequent wins; alphabetical breaks ties so the outcome doesn't depend on the order
        # rows happened to come back in.
        canonical[folded] = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    for folded, name in sorted(canonical.items()):
        match = _HEX.match(name)
        hex_code = f"#{match.group(1).lower()}" if match else None
        bind.execute(
            sa.text("INSERT INTO colours (name, hex_code) VALUES (:name, :hex_code)"),
            {"name": name, "hex_code": hex_code},
        )

    # Match on the folded, trimmed value so every spelling variant lands on the one row.
    bind.execute(
        sa.text(
            """
            UPDATE materials
               SET colour_id = (
                   SELECT c.id FROM colours c
                    WHERE LOWER(c.name) = LOWER(TRIM(materials.colour))
               )
             WHERE colour IS NOT NULL AND TRIM(colour) != ''
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # materials.colour was never dropped, so the text values are all still there and nothing
    # needs recovering. batch mode because dropping a column that carries a constraint means a
    # table rebuild on SQLite.
    with op.batch_alter_table('materials') as batch:
        batch.drop_column('colour_id')
    op.drop_table('colours')
