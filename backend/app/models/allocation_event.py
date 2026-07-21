import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AllocationEventType(str, enum.Enum):
    allocate = "allocate"
    deallocate = "deallocate"
    ship = "ship"
    auto_allocate = "auto_allocate"


class AllocationEvent(Base):
    """Append-only audit ledger for every stock-allocation state change — matters because
    this is a shared backend multiple devices can hit, so reconstructing "who allocated
    what, when" from current state alone wouldn't be possible without this."""

    __tablename__ = "allocation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[AllocationEventType] = mapped_column(
        PgEnum(AllocationEventType, name="allocation_event_type", create_type=False), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
