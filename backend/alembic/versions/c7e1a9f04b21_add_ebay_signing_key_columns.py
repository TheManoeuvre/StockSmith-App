"""add eBay signing key columns to platform_app_credentials

Stores the Ed25519 keypair from eBay's Key Management API, required to sign requests to
the APIs eBay gates behind Digital Signatures for EU/UK sellers (Sell Finances, which is
where StockSmith reads marketplace fees from). All nullable — an install that has not
minted a key yet keeps working exactly as before, minus the fee breakdown.

Revision ID: c7e1a9f04b21
Revises: b2c3d4e5f6a7
Create Date: 2026-08-06 11:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1a9f04b21'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('platform_app_credentials', sa.Column('signing_key_id', sa.String(), nullable=True))
    op.add_column('platform_app_credentials', sa.Column('signing_key_jwe', sa.String(), nullable=True))
    op.add_column('platform_app_credentials', sa.Column('signing_key_private', sa.String(), nullable=True))
    op.add_column(
        'platform_app_credentials', sa.Column('signing_key_expires_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'platform_app_credentials', sa.Column('signing_key_created_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('platform_app_credentials', 'signing_key_created_at')
    op.drop_column('platform_app_credentials', 'signing_key_expires_at')
    op.drop_column('platform_app_credentials', 'signing_key_private')
    op.drop_column('platform_app_credentials', 'signing_key_jwe')
    op.drop_column('platform_app_credentials', 'signing_key_id')
