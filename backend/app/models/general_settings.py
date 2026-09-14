import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.abc_classification import ABCClass
from app.models.base import Base, portable_enum


class CurrencyCode(str, enum.Enum):
    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"


class GeneralSettings(Base):
    """Single-row (id=1) shop-wide settings that don't fit elsewhere. default_currency
    only pre-fills the currency shown/stored on a new manual order (see routers/orders.py
    create_order) — it never triggers any FX conversion, currency here is just a label.

    The four forecast_* / default_lead_time_days fields drive the materials Weeks-of-
    Supply forecast (services/forecasting.py): warning/critical are the two dashboard
    thresholds in weeks of cover, lookback_weeks is the trailing window used to derive a
    sales/consumption rate, and default_lead_time_days (business days, Mon-Fri) estimates
    an arrival date for on-order purchase lines that were never given an explicit
    expected_arrival_date."""

    __tablename__ = "general_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Identifies this database's *lineage*, not this install. Generated once (lazily, see
    # services/system_status.py) and thereafter travels inside the database file — so a copy
    # restored from a backup carries the id of whatever database that backup was taken from.
    #
    # That is the whole point: connected clients poll /system/status and compare a fingerprint
    # built from this id. If it changes underneath them, every cached query they hold describes
    # a different database and has to be thrown away rather than merely refetched. Nullable
    # because databases predating this column exist and get one on first read.
    db_instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    default_currency: Mapped[CurrencyCode] = mapped_column(
        portable_enum(CurrencyCode, name="currency_code"),
        nullable=False,
        default=CurrencyCode.GBP,
    )
    forecast_warning_weeks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=6)
    forecast_critical_weeks: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=2)
    forecast_lookback_weeks: Mapped[int] = mapped_column(default=8, nullable=False)
    default_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    # The bottom of ABC's three levels: the tier an item gets when neither it nor its
    # category/type says otherwise. Two of them rather than one shared value because the
    # two catalogues differ in shape — see ABCScope. Both default to C, the
    # count-least tier, so switching this feature on doesn't declare the whole catalogue
    # due for counting every 30 days.
    default_material_abc_class: Mapped["ABCClass"] = mapped_column(
        portable_enum(ABCClass, name="abc_class"), nullable=False, default=ABCClass.C
    )
    default_product_abc_class: Mapped["ABCClass"] = mapped_column(
        portable_enum(ABCClass, name="abc_class"), nullable=False, default=ABCClass.C
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
