import enum
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform


class ReplacementParcelReason(str, enum.Enum):
    faulty_item = "faulty_item"
    missing_from_order = "missing_from_order"
    lost_in_transit = "lost_in_transit"
    damaged_in_transit = "damaged_in_transit"
    other = "other"
    # The default for a parcel the sync auto-created from a second marketplace label —
    # nobody has said yet why it went out. Paired with needs_review=True.
    unspecified = "unspecified"


class ReplacementParcelSource(str, enum.Enum):
    manual = "manual"
    sync = "sync"


class PostageChargeSource(str, enum.Enum):
    ebay_shipping_label = "ebay_shipping_label"
    etsy_ledger = "etsy_ledger"


class OrderReplacementParcel(Base):
    """A second (or third...) parcel sent against an order that has already shipped —
    a faulty item replaced, something missing from the box, a parcel lost in the post.

    Deliberately NOT modelled as extra OrderLines. Every OrderLine is sale demand: its
    CHECK constraints, reconcile_order_kitting's requirement sum, _recompute_order_status
    and the manual-order subtotal all assume that, so a replacement line there would
    re-reserve packaging, reopen the order and inflate what the buyer paid. A parcel is
    instead its own record with its own items (products and packaging materials alike),
    which are deducted from stock the moment the parcel is recorded — there is no draft
    state; recording it means it has gone.

    Postage: `postage_cost` is what the user typed. When a marketplace label charge is
    linked (OrderPostageCharge.replacement_parcel_id) that charge's amount is the figure
    profit uses and postage_cost is kept only as the user's own note. A sync-created
    parcel (source=sync) starts with needs_review=True and no items — the label was
    detected, but what went out and why is for the user to fill in.

    Items are never edited in place — a parcel is deleted (restocking everything) and
    recorded again. That keeps the stock-effect code to one forward and one reverse path.
    """

    __tablename__ = "order_replacement_parcels"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[ReplacementParcelReason] = mapped_column(
        portable_enum(ReplacementParcelReason, name="replacement_parcel_reason"),
        nullable=False,
        default=ReplacementParcelReason.unspecified,
    )
    source: Mapped[ReplacementParcelSource] = mapped_column(
        portable_enum(ReplacementParcelSource, name="replacement_parcel_source"),
        nullable=False,
        default=ReplacementParcelSource.manual,
    )
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    postage_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String, nullable=True)
    carrier: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    order: Mapped["Order"] = relationship(back_populates="replacement_parcels")  # noqa: F821
    items: Mapped[list["OrderReplacementParcelItem"]] = relationship(
        back_populates="parcel", cascade="all, delete-orphan", order_by="OrderReplacementParcelItem.id"
    )
    # The marketplace label this parcel's postage comes from, if one was matched. The FK
    # lives on the charge (a charge exists before, and without, any parcel — label #1 is
    # the original shipment), so this is the one-to-one seen from the parcel's side.
    charge: Mapped["OrderPostageCharge | None"] = relationship(back_populates="parcel", uselist=False)


class OrderReplacementParcelItem(Base):
    """One thing inside a replacement parcel: either a product/variant or a packaging
    material — exactly one of product_id/material_id is set. qty is Numeric so a material
    can be fractional (same reasoning as OrderLineReturn.qty); product items are always
    whole and validated as such at the API boundary.

    unit_cost_snapshot is frozen when the parcel is recorded — the build-BOM cost per unit
    for a product (compute_line_cost_snapshot, same source as OrderLine.cost_per_unit_
    snapshot) or the material's avg_unit_cost at that moment (same as
    OrderKittingAllocation.unit_cost_snapshot) — so a resend's cost of goods never drifts
    with later purchases."""

    __tablename__ = "order_replacement_parcel_items"
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_order_replacement_parcel_items_qty_positive"),
        CheckConstraint(
            "(product_id IS NOT NULL AND material_id IS NULL) OR (product_id IS NULL AND material_id IS NOT NULL)",
            name="ck_order_replacement_parcel_items_one_owner",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    parcel_id: Mapped[int] = mapped_column(
        ForeignKey("order_replacement_parcels.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=True)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True
    )
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True)
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    unit_cost_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)

    parcel: Mapped["OrderReplacementParcel"] = relationship(back_populates="items")
    product: Mapped["Product | None"] = relationship()  # noqa: F821
    variant: Mapped["ProductVariant | None"] = relationship()  # noqa: F821
    material: Mapped["Material | None"] = relationship()  # noqa: F821


class OrderPostageCharge(Base):
    """One shipping label the seller bought through the marketplace for this order, as
    reported by that marketplace's own financials (eBay Sell Finances SHIPPING_LABEL
    transactions; Etsy payment-account ledger entries). Written only by the sync
    (order_parcels.apply_postage_charges) — never by hand.

    `sequence` numbers the labels in the order they were bought: 1 is the original
    shipment (its amount replaces the shipping-profile estimate in net profit), 2+ is a
    resend and gets linked to — or spawns — an OrderReplacementParcel. Assigned once at
    insert and never renumbered, so a label surfacing late in a widened fetch window can't
    silently reassign which parcel an already-linked charge belongs to.

    `amount` is NULL for a label the marketplace confirms but won't cost per order — an
    eBay bulk label purchase, which comes back as one batch-total transaction with no
    orderId (ExternalPostageCharge / EbayAdapter._parse_shipping_labels). It keeps its
    place in the sequence, but as label #1 it does NOT replace the profile estimate, and
    linked to a parcel it defers to the user's typed postage_cost. A sync that later
    learns a stored label is a bulk one clears its amount (apply_postage_charges); the
    reverse never happens, since eBay never itemises a batch after the fact.

    Rows are never deleted by a sync that no longer returns them: a marketplace filter
    window shrinking is not evidence the label was refunded. Voided/refunded labels are
    not yet modelled (see docs/backlog.md)."""

    __tablename__ = "order_postage_charges"
    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_order_postage_charges_platform_external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    source: Mapped[PostageChargeSource] = mapped_column(
        portable_enum(PostageChargeSource, name="postage_charge_source"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    replacement_parcel_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_replacement_parcels.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="postage_charges")  # noqa: F821
    parcel: Mapped["OrderReplacementParcel | None"] = relationship(back_populates="charge")
