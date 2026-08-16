import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum


class StockTakeStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class StockTakeLineStatus(str, enum.Enum):
    """Where a line got to.

    `pending` and `counted` are the two live states while a take is open — the difference
    is simply whether counted_qty has been filled in. The other four are outcomes written
    by approve or by resolving a flagged line, and a line in any of them is finished
    unless someone resolves it again.

    `skipped` means no count was entered, so nothing was adjusted and the item's count
    date was deliberately NOT moved: leaving a line blank asserts nothing about it, and
    dating it would claim it had been verified.
    """

    pending = "pending"
    counted = "counted"
    applied = "applied"
    conflict = "conflict"
    accepted_system = "accepted_system"
    skipped = "skipped"


class StockTake(Base):
    """One counting session.

    Deliberately has no user column: StockSmith authenticates with a single shared
    password and has no users table, so there is nobody to attribute a take to. Recording
    a stub would look like provenance without being any.
    """

    __tablename__ = "stock_takes"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[StockTakeStatus] = mapped_column(
        portable_enum(StockTakeStatus, name="stock_take_status"),
        nullable=False,
        default=StockTakeStatus.open,
    )
    includes_materials: Mapped[bool] = mapped_column(default=False, nullable=False)
    includes_products: Mapped[bool] = mapped_column(default=False, nullable=False)
    overdue_only: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Rendered once at creation ("Materials in resin, filament") rather than recomputed on
    # read. The scope inputs that produced it can be edited or deleted afterwards — a
    # product type can be renamed or merged away — and the log should still say what was
    # actually counted at the time.
    scope_description: Mapped[str] = mapped_column(String, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["StockTakeLine"]] = relationship(
        back_populates="stock_take", cascade="all, delete-orphan"
    )


class StockTakeLine(Base):
    """One item to count, and what happened to it.

    Ownership mirrors StockAdjustment exactly — material_id, or product_id with an
    optional variant_id — because that is where stock actually lives: a product with
    active variants never accumulates its own current_stock, so its variants are the rows
    worth counting.

    counted_qty being NULL is load-bearing, not incidental. It is the difference between
    "counted it, it's zero" and "didn't get to this one", and the whole no-count-means-
    no-change-and-no-re-date rule rests on being able to tell those apart. It is why this
    is a nullable column rather than a defaulted one.
    """

    __tablename__ = "stock_take_lines"
    __table_args__ = (
        CheckConstraint(
            "(material_id IS NOT NULL AND product_id IS NULL AND variant_id IS NULL)"
            " OR (material_id IS NULL AND product_id IS NOT NULL)",
            name="ck_stock_take_lines_exactly_one_owner",
        ),
        CheckConstraint("counted_qty IS NULL OR counted_qty >= 0", name="ck_stock_take_lines_counted_nonneg"),
        UniqueConstraint("stock_take_id", "material_id", name="uq_stock_take_lines_take_material"),
        # An expression index rather than a plain UniqueConstraint on the three columns:
        # SQLite and Postgres both treat NULLs as distinct in a unique index, so a bare
        # constraint would happily allow two (take, product, NULL) rows — exactly the
        # duplicate this is meant to prevent, since NULL variant_id is the common case.
        Index(
            "uq_stock_take_lines_take_product_variant",
            "stock_take_id",
            "product_id",
            text("COALESCE(variant_id, -1)"),
            unique=True,
        ),
        Index("ix_stock_take_lines_stock_take_id", "stock_take_id"),
        # The unresolved-variances view filters on status across every closed take, so it
        # is the one lookup not scoped to a single take id.
        Index("ix_stock_take_lines_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_take_id: Mapped[int] = mapped_column(ForeignKey("stock_takes.id", ondelete="CASCADE"), nullable=False)

    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=True
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )

    # The snapshot taken when the take started — what the system believed then, not now.
    # Comparing this against the live quantity at approval is how movement mid-count is
    # detected, so it must never be refreshed.
    expected_qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    # Finished-goods lines only. Units allocated to open orders are typically picked and
    # boxed — physically off the shelf but still counted in current_stock until the order
    # ships — so a shelf count legitimately comes up short by this much. Snapshotted
    # rather than read live so the sheet, the CSV and the review all describe one moment.
    allocated_qty_at_start: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    counted_qty: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[StockTakeLineStatus] = mapped_column(
        portable_enum(StockTakeLineStatus, name="stock_take_line_status"),
        nullable=False,
        default=StockTakeLineStatus.pending,
    )
    # Recorded when a line is flagged, so the review screen can show all three numbers at
    # once: what was expected at the start, what was counted, and what the system says now.
    system_qty_at_approval: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    conflict_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # Two nullable FKs for two tables, the same shape ProductStockEvent uses for its three
    # sources. SET NULL rather than CASCADE: losing the adjustment should not delete the
    # record that this line was applied.
    material_adjustment_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_adjustments.id", ondelete="SET NULL"), nullable=True
    )
    stock_adjustment_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_adjustments.id", ondelete="SET NULL"), nullable=True
    )

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    stock_take: Mapped["StockTake"] = relationship(back_populates="lines")
