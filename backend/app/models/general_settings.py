import enum
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CurrencyCode(str, enum.Enum):
    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"


class GeneralSettings(Base):
    """Single-row (id=1) shop-wide settings that don't fit elsewhere. default_currency
    only pre-fills the currency shown/stored on a new manual order (see routers/orders.py
    create_order) — it never triggers any FX conversion, currency here is just a label."""

    __tablename__ = "general_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    default_currency: Mapped[CurrencyCode] = mapped_column(
        PgEnum(CurrencyCode, name="currency_code", create_type=False),
        nullable=False,
        default=CurrencyCode.GBP,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
