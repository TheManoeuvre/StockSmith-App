import enum
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform
from app.services.crypto import EncryptedString


class PlatformEnvironment(str, enum.Enum):
    production = "production"
    sandbox = "sandbox"


class PlatformAppCredential(Base):
    """Install-level marketplace developer-app credentials (Client ID/Secret + redirect
    config), one row per (platform, environment).

    Deliberately separate from PlatformConnection: this is the app-level OAuth client
    registration (the same for every shop that ever connects), not a per-shop OAuth
    grant. A packaged desktop app has no build pipeline to inject secrets per-install and
    no `.env` file a user would ever edit by hand — these live here instead, editable
    from Settings, encrypted at rest with the same Fernet key that already protects
    connection tokens (see app/services/crypto.py).

    `environment` only matters for eBay (Sandbox vs. Production use entirely separate
    keysets and API hosts) — Etsy always uses 'production'.
    """

    __tablename__ = "platform_app_credentials"
    __table_args__ = (UniqueConstraint("platform", "environment", name="uq_platform_app_credentials_platform_env"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    environment: Mapped[PlatformEnvironment] = mapped_column(
        portable_enum(PlatformEnvironment, name="platform_environment"),
        nullable=False,
        default=PlatformEnvironment.production,
    )
    client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    client_secret: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    # Etsy only: overrides settings.public_base_url when set, used to build the OAuth
    # redirect_uri (see services/platform_credentials.py). Left unset, Etsy falls back to
    # the .env-configured value for the dev loop.
    public_base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # eBay only: the RuName eBay assigns to a redirect configuration registered in its
    # dev portal — overrides settings.ebay_ru_name when set. Not a URL; see
    # EbayAdapter.build_authorize_url's own docstring for why eBay's redirect_uri isn't
    # one.
    ru_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
