"""Storage locations and where stock is placed in them (docs/plan-locations.md §3).

Locations are a free-form tree; placements say how much of an item is in each one. Neither ever
changes an item's total — they sit beside the stock ledger, not in it. "Unassigned" is the item's
total minus the sum of its placements and is never stored.
"""

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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum


class Location(Base):
    """One place stock can be kept: a room, a shelf, a box.

    The full path (``Office › Shelf 01 › Grey Box 03``) is computed from the tree on read, never
    stored, so a rename or move cannot leave stale paths behind. ``code`` is what the barcode
    carries and never changes, so printed labels survive both.
    """

    __tablename__ = "locations"
    __table_args__ = (
        # NULL parents are distinct to a plain UNIQUE, so top-level uniqueness needs its own
        # partial index. Names compare case-insensitively ("Shelf 1" and "shelf 1" are one place).
        Index(
            "uq_locations_sibling_name",
            "parent_id",
            text("lower(name)"),
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        Index(
            "uq_locations_top_level_name",
            text("lower(name)"),
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: a location with children cannot be deleted out from under them.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # LOC-000042. Assigned from the row id at creation, unique, immutable.
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # A location that has ever held stock is archived rather than deleted. Archiving needs it empty.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockPlacement(Base):
    """"This much of this item is in this location."

    Ownership mirrors StockAdjustment: a material, or a product with an optional variant (a
    product that has variants is placed per variant, because the variants hold the stock).
    """

    __tablename__ = "stock_placements"
    __table_args__ = (
        CheckConstraint("qty >= 0", name="ck_stock_placements_qty_nonneg"),
        CheckConstraint(
            "(material_id IS NOT NULL AND product_id IS NULL AND variant_id IS NULL)"
            " OR (material_id IS NULL AND product_id IS NOT NULL)",
            name="ck_stock_placements_one_owner",
        ),
        UniqueConstraint("location_id", "material_id", name="uq_stock_placements_location_material"),
        # (location, product, variant) — NULL variants are distinct to a plain UNIQUE, so the
        # bare-product case has its own partial index below.
        UniqueConstraint("location_id", "product_id", "variant_id", name="uq_stock_placements_location_variant"),
        Index(
            "uq_stock_placements_location_product",
            "location_id",
            "product_id",
            unique=True,
            sqlite_where=text("variant_id IS NULL AND product_id IS NOT NULL"),
            postgresql_where=text("variant_id IS NULL AND product_id IS NOT NULL"),
        ),
        # At most one primary placement per item.
        Index(
            "uq_stock_placements_primary_material",
            "material_id",
            unique=True,
            sqlite_where=text("is_primary AND material_id IS NOT NULL"),
            postgresql_where=text("is_primary AND material_id IS NOT NULL"),
        ),
        Index(
            "uq_stock_placements_primary_product",
            "product_id",
            text("coalesce(variant_id, 0)"),
            unique=True,
            sqlite_where=text("is_primary AND product_id IS NOT NULL"),
            postgresql_where=text("is_primary AND product_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlacementEventKind(str, enum.Enum):
    place = "place"
    move = "move"
    remove = "remove"
    auto_deduct = "auto_deduct"
    auto_add = "auto_add"
    count = "count"


class PlacementEvent(Base):
    """Append-only history of everything that touches a placement.

    The audit trail ("where did it go?") and what a location's History section shows. It is not
    read to compute any quantity. Locations are referenced with SET NULL so history survives
    deleting a never-used location, but a location that ever appears here is archived instead.
    """

    __tablename__ = "placement_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int | None] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True
    )
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    from_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    to_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    qty: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    kind: Mapped[PlacementEventKind] = mapped_column(
        portable_enum(PlacementEventKind, name="placement_event_kind"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
