"""Work flow: the steps between "an order arrived" and "it shipped", defined by the seller.

See docs/plan-workflow.md. Three ideas:

* A **stage** is a named step ("Pre-clean", "Mask", "Assemble"). Two are built in and cannot be
  deleted because they connect to logic that already exists: **Make** (completing it is a
  `Build`) and **Ship** (satisfied by the order flow). Everything else is the seller's own.
* A **route** is an ordered list of stages. Routes attach to a product category with a per-product
  deviation, the same shape ABC classification uses, and resolve in `services/work_routes.py`.
* A **work event** is a signed count of units that completed a stage. Nothing else about progress
  is stored — what to do next is derived from events, orders, builds and stock in
  `services/work.py`.

Make and Ship never get events: Make is read from `builds`, Ship from the order itself.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, portable_enum


class WorkStageKind(str, enum.Enum):
    """`make` and `ship` are the two anchors every route must contain exactly once, with ship
    last. Everything before make is preparation; everything between them is finishing."""

    custom = "custom"
    make = "make"
    ship = "ship"


class WorkQtyBasis(str, enum.Enum):
    """What one unit of a stage's work is counted in.

    `unit`: one per finished item. `bom_category`: one per unit of the product's BOM lines that
    belong to a material category — so a 6-pack whose BOM holds six blanks shows six to clean and
    mask, and a 2-pack shows two.
    """

    unit = "unit"
    bom_category = "bom_category"


class WorkStage(Base):
    __tablename__ = "work_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Free text on purpose: a station is only a grouping for the Work page ("Desk", "Bench",
    # "Printers", "Pack"). The app suggests what already exists but never restricts it.
    station: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[WorkStageKind] = mapped_column(
        portable_enum(WorkStageKind, name="work_stage_kind"), nullable=False, default=WorkStageKind.custom
    )
    # Work that only makes sense for one buyer ("make the file" for a personalised item). Never
    # pooled, never done ahead — progress is recorded against the order line.
    per_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Drying/curing time before the next stage may start, and a typical duration. Both are
    # recorded now and used by the later timing phase; neither changes what is listed today.
    wait_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    typical_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("wait_minutes IS NULL OR wait_minutes >= 0", name="ck_work_stages_wait_nonneg"),
        CheckConstraint("typical_minutes IS NULL OR typical_minutes >= 0", name="ck_work_stages_typical_nonneg"),
    )


class WorkRoute(Base):
    __tablename__ = "work_routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Exactly one route is the shop default — what a product gets when neither it nor its
    # category names one. Enforced by a partial unique index in the migration.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    steps: Mapped[list["WorkRouteStep"]] = relationship(
        back_populates="route", cascade="all, delete-orphan", order_by="WorkRouteStep.position"
    )


class WorkRouteStep(Base):
    """One stage's place in one route, plus the settings that belong to *that use* of the stage.

    The library stage ("Mask") is shared; what it counts in and whether designs may mix on it
    differ by route, so they live here. A route inherits per category with a per-product
    deviation, so these follow the same inheritance with no mechanism of their own.
    """

    __tablename__ = "work_route_steps"
    __table_args__ = (
        UniqueConstraint("route_id", "stage_id", name="uq_work_route_steps_route_stage"),
        UniqueConstraint("route_id", "position", name="uq_work_route_steps_route_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("work_routes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # RESTRICT: a stage in a route can't be deleted from under it.
    stage_id: Mapped[int] = mapped_column(ForeignKey("work_stages.id", ondelete="RESTRICT"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # When on, one batch may combine variants of a product that print from the same materials
    # (several designs on one plate). Off by default — a mixed plate has to be sorted afterwards.
    mix_variants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qty_basis: Mapped[WorkQtyBasis] = mapped_column(
        portable_enum(WorkQtyBasis, name="work_qty_basis"), nullable=False, default=WorkQtyBasis.unit
    )
    qty_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_categories.id", ondelete="SET NULL"), nullable=True
    )

    route: Mapped["WorkRoute"] = relationship(back_populates="steps")
    stage: Mapped["WorkStage"] = relationship()


class WorkEvent(Base):
    """Append-only: "n units completed this stage". Negative to undo.

    `qty` is in the stage's own units (blanks for a bom_category stage, finished items for a
    unit stage), because that is what the seller counted. Pooled work — nearly everything — has
    no order line and is keyed by product and variant; a per-order stage carries the line it was
    done for. Make and Ship are never recorded here.
    """

    __tablename__ = "work_events"
    __table_args__ = (
        CheckConstraint("qty != 0", name="ck_work_events_qty_nonzero"),
        Index("ix_work_events_product_stage", "product_id", "stage_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=True
    )
    order_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stage_id: Mapped[int] = mapped_column(ForeignKey("work_stages.id", ondelete="RESTRICT"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # Nullable columns for the integration hooks the plan reserves (§7). Manual entries leave both null.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
