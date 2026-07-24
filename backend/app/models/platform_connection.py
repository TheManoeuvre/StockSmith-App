from datetime import date, datetime, timedelta

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform
from app.models.platform_credential import PlatformEnvironment
from app.services.crypto import EncryptedString


def _default_sync_start_date() -> date:
    return date.today() - timedelta(days=14)


class PlatformConnection(Base):
    """OAuth connection state for one marketplace, one row per platform.

    Tokens live here rather than in .env because refresh tokens rotate at runtime and
    need to be written back on every use — .env isn't runtime-writable. Static app
    credentials (client id/secret) live in PlatformAppCredential instead (see
    services/platform_credentials.py) — a separate table because those are shared across
    every shop that ever connects, not tied to one OAuth grant.

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
    # Which keyset/host this connection's tokens belong to — only meaningful for eBay
    # (Etsy has no sandbox). One PlatformConnection row per platform still means only one
    # environment can be connected at a time; switching requires disconnecting first, by
    # design — this app tests against Sandbox, then cuts over to Production, rather than
    # tracking both simultaneously (see docs/plan-marketplace-integrations.md Section 2).
    environment: Mapped[PlatformEnvironment] = mapped_column(
        portable_enum(PlatformEnvironment, name="platform_environment"),
        nullable=False,
        default=PlatformEnvironment.production,
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
    # Background sync scheduler settings (services/sync_scheduler.py) — off by default so
    # a freshly-connected shop doesn't start unattended commits before the user has run at
    # least one manual preview/sync to sanity-check the adapter's parsing against this
    # real shop's data (see EtsyAdapter's own docstrings on why that matters).
    auto_sync_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    sync_interval_minutes: Mapped[int] = mapped_column(default=15, nullable=False)
    # Consecutive PlatformAuthError count from the background scheduler only (manual
    # syncs don't touch this) — reset to 0 on any success, and once it crosses
    # sync_scheduler._MAX_CONSECUTIVE_AUTH_FAILURES the scheduler flips auto_sync_enabled
    # back off itself rather than retrying a dead connection forever.
    consecutive_auth_failures: Mapped[int] = mapped_column(default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_connected(self) -> bool:
        return self.refresh_token is not None
