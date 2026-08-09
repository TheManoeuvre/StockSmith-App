from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BackupSettings(Base):
    """Single-row (id=1) backup configuration.

    Scheduling defaults to ON. The whole value of this feature is that a backup exists when
    something goes wrong, and a backup you have to remember to take is the one you didn't take.

    Note what is *not* here: the backups themselves. The filesystem is the source of truth —
    listing means globbing `backups/*.zip` and reading each archive's manifest. Rows describing
    files drift the moment someone deletes one in Explorer, and there is no question the app
    needs to answer about a backup that isn't answerable from the file. The trade-off is no
    history of deleted backups; the log has that.

    secondary_dir is how backups get off the host machine without the app growing cloud
    credentials: point it at a OneDrive/Dropbox folder and their sync client does the rest. The
    last_ok/last_error pair beside it is not optional garnish — a synced folder that silently
    stops existing (unlinked account, renamed path) would otherwise leave someone believing they
    have off-host copies they don't have.
    """

    __tablename__ = "backup_settings"
    __table_args__ = (
        CheckConstraint("scheduled_hour_local >= 0 AND scheduled_hour_local <= 23", name="ck_backup_hour_range"),
        CheckConstraint("retention_count >= 1", name="ck_backup_retention_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    scheduled_hour_local: Mapped[int] = mapped_column(nullable=False, default=3)
    retention_count: Mapped[int] = mapped_column(nullable=False, default=7)

    secondary_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    secondary_dir_last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    secondary_dir_last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_error: Mapped[str | None] = mapped_column(String, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
