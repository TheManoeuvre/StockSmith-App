"""drop listing_profiles.is_default, assigning the old default to the products that used it

A listing profile decides the category, processing time and policies a listing goes out
with. Until now a platform had a "default" profile that every product without an explicit
choice silently used, so a product could be drafted under whichever profile happened to
carry the flag without anyone having decided that for it. The flag and its partial unique
index go; a product now uses the profile chosen for it or none, and "none" is reported by
the readiness check as a blocker until one is picked.

Before the flag goes, every product that was resolving to a platform's default — a
settings row for that platform with no profile, or no settings row at all — is pointed at
that default explicitly. That is exactly what those products were drafting under
yesterday, so nothing changes for them on upgrade; the difference is that from now on the
choice is visible on the product and can be changed there. Products without a settings
row get one, because "no row" was itself a way of resolving to the default.

Revision ID: b7e4d1c92a05
Revises: a9f3c2d7e815
Create Date: 2026-09-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4d1c92a05'
down_revision: Union[str, Sequence[str], None] = 'a9f3c2d7e815'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assign_defaults_explicitly() -> None:
    bind = op.get_bind()
    defaults = bind.execute(
        sa.text("SELECT platform, id FROM listing_profiles WHERE is_default = 1")
    ).fetchall()
    for platform, profile_id in defaults:
        params = {"platform": platform, "profile_id": profile_id}
        bind.execute(
            sa.text(
                "UPDATE product_platform_settings SET listing_profile_id = :profile_id "
                "WHERE platform = :platform AND listing_profile_id IS NULL"
            ),
            params,
        )
        bind.execute(
            sa.text(
                "INSERT INTO product_platform_settings (product_id, platform, listing_profile_id) "
                "SELECT p.id, :platform, :profile_id FROM products p "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM product_platform_settings s "
                "  WHERE s.product_id = p.id AND s.platform = :platform"
                ")"
            ),
            params,
        )


def upgrade() -> None:
    """Upgrade schema."""
    _assign_defaults_explicitly()
    op.drop_index('uq_listing_profiles_platform_default', table_name='listing_profiles')
    with op.batch_alter_table('listing_profiles') as batch:
        batch.drop_column('is_default')


def downgrade() -> None:
    """Downgrade schema.

    The explicit assignments are left in place: they are true choices now, and a product
    pointed at the profile it was already using is the same outcome the default gave."""
    with op.batch_alter_table('listing_profiles') as batch:
        batch.add_column(sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.create_index(
        'uq_listing_profiles_platform_default',
        'listing_profiles',
        ['platform'],
        unique=True,
        sqlite_where=sa.text('is_default IS 1'),
        postgresql_where=sa.text('is_default'),
    )
