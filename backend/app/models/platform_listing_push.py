import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class ListingPushStatus(str, enum.Enum):
    success = "success"
    error = "error"


class PlatformListingPush(Base):
    """Append-only log of every outbound quantity-push attempt (services/listing_push.py)
    — the push analog of PlatformSyncRun, which only ever logs inbound order-sync
    attempts. Exists specifically so a persistently failing push is visible somewhere
    (a stale marketplace quantity is a real overselling risk, not just cosmetic
    staleness) without blocking or being blocked by order sync."""

    __tablename__ = "platform_listing_pushes"
    __table_args__ = (
        # Supports the "latest attempt per listing" window in services/sync_status.py,
        # which the polled menu-bar indicator runs every 60s.
        Index(
            "ix_platform_listing_pushes_target_attempted_at",
            "platform",
            "product_id",
            "variant_id",
            "attempted_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    attempted_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ListingPushStatus] = mapped_column(
        portable_enum(ListingPushStatus, name="listing_push_status"), nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
