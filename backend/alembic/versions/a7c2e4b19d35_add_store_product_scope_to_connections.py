"""add per-store product scope settings to platform_connections

Backs the per-product store on/off switch (docs/plan-store-product-toggles.md):
new_products_default_on says whether a newly created product starts on for the store,
and product_scope_decided_at records that the user has answered the one-off "turn every
product on or off for this store?" prompt, so it is never asked again — including after a
disconnect/reconnect of the same shop.

Revision ID: a7c2e4b19d35
Revises: 95c15157d55b
Create Date: 2026-10-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c2e4b19d35'
down_revision: Union[str, Sequence[str], None] = '95c15157d55b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('platform_connections') as batch_op:
        batch_op.add_column(
            sa.Column('new_products_default_on', sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(sa.Column('product_scope_decided_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('platform_connections') as batch_op:
        batch_op.drop_column('product_scope_decided_at')
        batch_op.drop_column('new_products_default_on')
