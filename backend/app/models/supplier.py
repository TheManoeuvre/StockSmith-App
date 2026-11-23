from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    website_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # How many business days (Mon-Fri) this supplier typically takes to deliver after an order
    # is placed. NULL means "no supplier-specific figure" — the forecast then falls back to the
    # shop-wide GeneralSettings.default_lead_time_days. Feeds two things in
    # services/forecasting.py: the arrival date estimated for an on-order line with no explicit
    # expected_arrival_date (stepped forward skipping weekends), and the reorder point — a
    # material's weeks-of-supply is judged against warning/critical thresholds *plus* its lead
    # time (converted to weeks at 5 business days/week), so 8 weeks of cover with a 10-business-
    # day lead is treated the same as 6 weeks with none.
    default_lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The supplier's logo, shown beside its name on purchases. One file, relative to the asset
    # root. logo_source says where it came from: "favicon" (fetched from website_url, replaced
    # whenever the URL changes or is refreshed) or "upload" (chosen by the user, never touched
    # by a favicon fetch — removing it falls back to the favicon). NULL path means no logo.
    logo_path: Mapped[str | None] = mapped_column(String, nullable=True)
    logo_source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Bumped on every change to the logo. The file always has the same name, so clients use this
    # as the cache key — and as the "has a logo" signal without needing the path.
    logo_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
