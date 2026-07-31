import enum
from datetime import datetime

from sqlalchemy import DateTime, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class CurrencyCode(str, enum.Enum):
    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"


class GeneralSettings(Base):
    """Single-row (id=1) shop-wide settings that don't fit elsewhere. default_currency
    only pre-fills the currency shown/stored on a new manual order (see routers/orders.py
    create_order) — it never triggers any FX conversion, currency here is just a label.

    The four forecast_* / default_lead_time_weeks fields drive the materials Weeks-of-
    Supply forecast (services/forecasting.py): warning/critical are the two dashboard
    thresholds in weeks of cover, lookback_weeks is the trailing window used to derive a
    sales/consumption rate, and default_lead_time_weeks estimates an arrival date for
    on-order purchase lines that were never given an explicit expected_arrival_date."""

    __tablename__ = "general_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    default_currency: Mapped[CurrencyCode] = mapped_column(
        portable_enum(CurrencyCode, name="currency_code"),
        nullable=False,
        default=CurrencyCode.GBP,
    )
    forecast_warning_weeks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=6)
    forecast_critical_weeks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=2)
    forecast_lookback_weeks: Mapped[int] = mapped_column(default=8, nullable=False)
    default_lead_time_weeks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=4)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
