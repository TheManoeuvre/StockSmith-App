from datetime import date, datetime, timedelta

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform
from app.services.crypto import EncryptedString


def _default_sync_start_date() -> date:
    return date.today() - timedelta(days=14)


class PlatformConnection(Base):
    """OAuth connection state for one marketplace, one row per platform.

    Tokens live here rather than in .env because refresh tokens rotate at runtime and
    need to be written back on every use — .env isn't runtime-writable. Static app
    credentials (client id/secret) stay in config.py/.env since those never change.

    access_token/refresh_token are encrypted at rest (see app/services/crypto.py) — the
    underlying column is still a plain string column, so no migration is needed to
    adopt/rotate this, but existing plaintext rows won't decrypt once a key is set and
    must be reconnected.
    """

    __tablename__ = "platform_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False, unique=True
    )
    access_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(String, nullable=True)
    external_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Etsy-only enrichment (best-effort, fetched from GET /v3/application/shops/{shop_id}
    # after external_account_id is resolved) — shop_name is the human-readable store
    # name, shop_icon_path is a relative path under asset_root to a locally-downloaded
    # copy of the shop's icon (see app/services/file_storage.save_platform_icon).
    shop_name: Mapped[str | None] = mapped_column(String, nullable=True)
    shop_icon_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # Floor on order sync — never import orders placed before this date, regardless of
    # last_orders_synced_at. Defaults to 14 days before the connection is first created,
    # so a first sync doesn't pull in a shop's entire historical order backlog. Editable
    # afterward from the Etsy sync panel.
    sync_start_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=_default_sync_start_date)
    last_orders_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_connected(self) -> bool:
        return self.refresh_token is not None
