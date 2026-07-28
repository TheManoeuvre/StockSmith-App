import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, text
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
    # Orders the marketplace returned that were NOT imported because payment hasn't
    # settled (order_sync._partition_new_orders). Without this the gate is invisible: a
    # skipped order and an order that never existed produce identical log rows, so a gate
    # that has silently started rejecting everything — the failure mode that actually
    # matters, since fail-closed defaults mean the symptom is "no orders appear" — looks
    # exactly like a quiet week. `fetched=12, new=0, skipped_unpaid=12` is unmistakable.
    skipped_unpaid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
