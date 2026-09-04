import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum


class PurchaseStatus(str, enum.Enum):
    ordered = "ordered"
    partially_received = "partially_received"
    received = "received"


class Purchase(Base):
    """A purchase order from a supplier, covering one or more materials at once.

    Recording a purchase does not affect stock/cost until some of it is received, and
    "received" is per line and per delivery — see MaterialPurchaseReceipt. Nothing about
    stock or cost reads `status`; services/costing.py's recompute_material replays the
    receipt rows themselves.
    """

    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True)
    # The supplier's own reference for this order (their PO/order/invoice number), as printed
    # on their paperwork. Free text — every supplier formats it differently — and optional.
    # The UI leads with it on the list because it is the number a human quotes when chasing a
    # delivery; our own `id` stays the stable key everything else joins on.
    supplier_order_number: Mapped[str | None] = mapped_column(String, nullable=True)
    order_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    # Derived, never set directly: refresh_purchase_status() in services/purchase_receipts.py
    # is the only writer, and it works the value out from the lines. Kept as a stored column
    # purely so the list endpoint can filter on it and the UI can show a pill — no stock or
    # costing query reads it.
    status: Mapped[PurchaseStatus] = mapped_column(
        portable_enum(PurchaseStatus, name="purchase_status"),
        nullable=False,
        default=PurchaseStatus.ordered,
    )
    # When the order was *completed*, i.e. the last receipt that finished it off. NULL while
    # anything is still outstanding. This is no longer the timestamp anything is costed at —
    # each receipt carries its own, which is the whole point of splitting them out.
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional ETA, entered by the user at PO time. When absent, the materials forecast
    # (services/forecasting.py) estimates arrival as order_date + GeneralSettings.
    # default_lead_time_days instead — this column is never back-filled with that
    # estimate, so "unknown ETA" stays distinguishable from "known ETA."
    expected_arrival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["MaterialPurchase"]] = relationship(back_populates="purchase", cascade="all, delete-orphan")
    supplier: Mapped["Supplier | None"] = relationship()

    @property
    def supplier_name(self) -> str | None:
        return self.supplier.name if self.supplier else None


class MaterialPurchase(Base):
    """A single material line item within a Purchase order.

    `qty` is what was *ordered* and is never rewritten to match what turned up — a short
    delivery is recorded by closing the line (closed_at), so the difference between ordered
    and received stays visible instead of being edited away.
    """

    __tablename__ = "material_purchases"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_material_purchases_qty_positive"),
        CheckConstraint("total_cost >= 0", name="ck_material_purchases_total_cost_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    # The line total for the full ordered qty, not a unit price. A receipt that brings in
    # part of the line takes a pro-rata share of this unless it carries its own cost —
    # see services/costing.py's replay.
    total_cost: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    # Set when the rest of this line is never coming: the supplier shipped 7 of 10 and
    # that's that. Takes the remainder out of "on order" without pretending 7 was ordered.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    purchase: Mapped["Purchase"] = relationship(back_populates="lines")
    material: Mapped["Material"] = relationship(back_populates="purchase_lines")
    receipts: Mapped[list["MaterialPurchaseReceipt"]] = relationship(
        back_populates="line", cascade="all, delete-orphan", order_by="MaterialPurchaseReceipt.id"
    )

    # Both read `receipts`, so both need it loaded — every read path selectinloads it. They
    # live here rather than in services/purchase_receipts.py so PurchaseLineRead can pick
    # them up straight off the attribute, the same way supplier_name works on Purchase.
    @property
    def received_qty(self) -> Decimal:
        return sum((Decimal(r.qty) for r in self.receipts), Decimal(0))

    @property
    def outstanding_qty(self) -> Decimal:
        """What is still to come. Zero once the line is closed, whatever went undelivered."""
        if self.closed_at is not None:
            return Decimal(0)
        remaining = Decimal(self.qty) - self.received_qty
        return remaining if remaining > 0 else Decimal(0)


class MaterialPurchaseReceipt(Base):
    """One physical arrival of part (or all) of a purchase line.

    This is the row material stock and cost are actually derived from. A line ordered 10
    and delivered 6-then-4 produces two receipts with two dates, and the weighted average
    in services/costing.py picks each up at its own point in the timeline — which is the
    thing a single received_at on the order could never express.

    total_cost NULL means "this delivery's pro-rata share of the line", which is the normal
    case. Set it only when the supplier billed this delivery separately for something other
    than its share; doing so on any receipt of a line switches that line off the remainder
    sweep, so the shares no longer have to add up to the line total.
    """

    __tablename__ = "material_purchase_receipts"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_material_purchase_receipts_qty_positive"),
        CheckConstraint(
            "total_cost IS NULL OR total_cost >= 0", name="ck_material_purchase_receipts_total_cost_nonneg"
        ),
        Index("ix_material_purchase_receipts_line", "purchase_line_id"),
        Index("ix_material_purchase_receipts_batch", "batch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_line_id: Mapped[int] = mapped_column(
        ForeignKey("material_purchases.id", ondelete="CASCADE"), nullable=False
    )
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    total_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # One value per receive call, so "undo that delivery" is unambiguous. Grouping by equal
    # received_at would work only for as long as the server stamps it — it falls apart the
    # first time someone back-dates two separate deliveries to the same day.
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    line: Mapped["MaterialPurchase"] = relationship(back_populates="receipts")
