import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.listing import ListingPlatform


class OrderStatus(str, enum.Enum):
    pending = "pending"
    allocated = "allocated"
    shipped = "shipped"
    cancelled = "cancelled"


class Order(Base):
    """A customer order, placed either manually or pulled from a marketplace (Etsy etc).

    status is a cheap rollup recomputed from line quantities by the allocation service —
    never mutated independently. platform/external_order_id are NULL for manually-entered
    orders; Postgres treats multiple NULLs as distinct, so the uniqueness constraint below
    doesn't block manual orders from coexisting.
    """

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("platform", "external_order_id", name="uq_orders_platform_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform | None] = mapped_column(
        PgEnum(ListingPlatform, name="listing_platform", create_type=False), nullable=True
    )
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        PgEnum(OrderStatus, name="order_status", create_type=False), nullable=False, default=OrderStatus.pending
    )
    buyer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    buyer_note: Mapped[str | None] = mapped_column(String, nullable=True)
    order_placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Buyer-facing totals straight off the marketplace receipt — always available at
    # import time, refreshed on every later sync (see order_sync._upsert_order).
    grand_total: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    shipping_charged: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    tax_charged: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    vat_charged: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    discount_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    refunded_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)

    # Etsy's own payment breakdown (getShopPaymentByReceiptId) — a separate API call from
    # the receipt, so these stay NULL until the order's payment has settled.
    payment_fees: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    payment_net: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    payment_status: Mapped[str | None] = mapped_column(String, nullable=True)
    financials_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Self-healing flag set by order_sync when a sync can't fully reconcile this order
    # (e.g. Etsy shows it shipped but nothing is allocated locally) — cleared automatically
    # once that reconciliation succeeds on a later sync. See order_sync._reconcile_status.
    sync_issue: Mapped[str | None] = mapped_column(String, nullable=True)

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    """A single product/variant line within an Order, tracking fulfillment through three
    monotonic-ish counters: ordered_qty (what was bought), allocated_qty (how much on-hand
    stock is currently reserved for it — can go up and down as stock is allocated/
    deallocated), shipped_qty (how much has actually left the building — only increases).

    product_id/variant_id are nullable to support needs_mapping lines pulled from a
    marketplace whose SKU didn't match anything in our catalog yet — sku holds the raw
    text so the user has something to map from.
    """

    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("ordered_qty > 0", name="ck_order_lines_ordered_qty_positive"),
        CheckConstraint(
            "allocated_qty >= 0 AND allocated_qty <= ordered_qty", name="ck_order_lines_allocated_qty_range"
        ),
        CheckConstraint(
            "shipped_qty >= 0 AND shipped_qty <= allocated_qty", name="ck_order_lines_shipped_qty_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True
    )
    ordered_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shipped_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    external_line_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sku: Mapped[str | None] = mapped_column(String, nullable=True)
    needs_mapping: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Build-BOM and kitting-BOM cost per unit, snapshotted once when the line is created
    # (see order_costs.compute_line_cost_snapshot) — deliberately frozen at that point,
    # not recomputed later, so a historical order's cost-of-goods doesn't drift as
    # material costs change.
    cost_per_unit_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
    kitting_cost_per_unit_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="lines")
    product: Mapped["Product | None"] = relationship()
    variant: Mapped["ProductVariant | None"] = relationship()
