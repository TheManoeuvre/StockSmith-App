import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class SyncRunMode(str, enum.Enum):
    preview = "preview"
    commit = "commit"


class SyncRunStatus(str, enum.Enum):
    success = "success"
    error = "error"


class PlatformSyncRun(Base):
    """Append-only log of every order-sync attempt (preview or commit), so the user can
    review sync activity/success/failure history while running Phase C in manual,
    observe-before-trust mode — this table exists specifically to serve that, not just
    for debugging."""

    __tablename__ = "platform_sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    mode: Mapped[SyncRunMode] = mapped_column(
        portable_enum(SyncRunMode, name="sync_run_mode"), nullable=False
    )
    status: Mapped[SyncRunStatus] = mapped_column(
        portable_enum(SyncRunStatus, name="sync_run_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_mapping_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
