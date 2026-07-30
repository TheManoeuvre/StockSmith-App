import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class ProductStockEventType(str, enum.Enum):
    build_success = "build_success"
    build_failed = "build_failed"
    adjustment = "adjustment"
    order_fulfillment = "order_fulfillment"


class ProductStockEvent(Base):
    """Unified, append-only ledger row for every event that touches (or, for a failed
    build, deliberately does not touch) a product/variant's current_stock — the "Stock"
    history view combines what used to be two separate tables (builds, stock
    adjustments) plus order fulfillment, which previously wasn't logged as a
    finished-goods event at all.

    One row per event, each carrying running_balance — current_stock immediately after
    this event — so the history reads as a running total without the UI (or a caller)
    needing to replay anything itself. Denormalized at write time rather than replayed
    from history on read, matching how current_stock itself is already a mutate-in-place
    counter rather than an event-sourced value (see Product/ProductVariant.current_stock)
    — full event-replay is a documented follow-up, not part of this pass.

    Exactly one of source_build_id/source_adjustment_id/source_order_line_id is set,
    matching event_type, so the UI can link back to the originating record (and its own
    detail — qty_built/qty_failed, adjustment mode, order reference) without this table
    needing to duplicate those fields itself.

    A build_failed row always carries qty_delta = 0 — a failed build consumes material
    (see BuildFailedConsumption) but never becomes finished-goods stock, so current_stock
    (and therefore running_balance) is unaffected. It still gets its own row so the
    failure — and the material burned on it — is visible in the same timeline as
    everything else that touches this product, rather than being invisible to anyone
    reading the stock history.
    """

    __tablename__ = "product_stock_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[ProductStockEventType] = mapped_column(
        portable_enum(ProductStockEventType, name="product_stock_event_type"), nullable=False
    )
    qty_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    running_balance: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    source_build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"), nullable=True)
    source_adjustment_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_adjustments.id", ondelete="SET NULL"), nullable=True
    )
    source_order_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_lines.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
