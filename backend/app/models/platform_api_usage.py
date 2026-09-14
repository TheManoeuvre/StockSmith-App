from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class PlatformApiUsage(Base):
    """How many marketplace API calls StockSmith has made to one platform on one UTC day.

    One row per (platform, date). Written by services/platform_api_usage.py, which counts
    every HTTP round-trip an adapter makes (services/platforms/*._request_once) into an
    in-memory delta and folds those deltas into this table on a timer — so the hot path
    stays a dict increment, not a write.

    Exists so listing_push can back off automatic pushes before it exhausts a platform's
    daily budget and starves order sync — the failure the 2026-09-07 stall made concrete.
    Order sync itself never consults this; only the outbound-push and reconcile paths do.

    Not pruned automatically: a row per platform per day is a few hundred rows a year, and
    the history is useful for spotting which day a budget blew and why.
    """

    __tablename__ = "platform_api_usage"

    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), primary_key=True
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
