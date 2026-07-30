from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Build(Base):
    """A finished-goods production event: qty_built units of a product (or one of its
    variants) were physically built just now, consuming materials per the resolved BOM.

    Atomic and immediate — unlike Purchase, there's no ordered/received lifecycle, since
    a build represents work already done. One product/variant per row; building several
    products at once is just several Build rows.

    qty_failed tracks units attempted but not produced (e.g. a 3D print that failed
    partway) — material was consumed without becoming finished-goods stock, so it needs
    its own accounting. Which BOM lines actually got consumed for the failed qty (a
    failed print may not have burned through the whole BOM) is recorded per-line in
    BuildFailedConsumption rather than assumed from the build's own BOM, since a partial
    failure's consumption doesn't necessarily mirror a successful build's.
    """

    __tablename__ = "builds"
    __table_args__ = (
        CheckConstraint("qty_built >= 0", name="ck_builds_qty_built_nonneg"),
        CheckConstraint("qty_failed >= 0", name="ck_builds_qty_failed_nonneg"),
        CheckConstraint("qty_built > 0 OR qty_failed > 0", name="ck_builds_qty_built_or_failed_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id", ondelete="RESTRICT"), nullable=True)
    qty_built: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildFailedConsumption(Base):
    """Per-BOM-line record of what actually got consumed for a build's failed qty — a
    failed print may only have burned through part of the BOM (e.g. filament laid down
    before the print died, but a hardware insert never reached), so this is recorded
    explicitly per line rather than assumed from the successful-build BOM.

    was_consumed is the user's checkbox selection at record-failure time (filament
    defaults checked, everything else unchecked, per the build form). qty_consumed is
    only meaningful when was_consumed is True — the qty actually deducted via a
    MaterialAdjustment, mirroring a successful build's qty_required * qty_failed, but
    kept as an explicit column rather than recomputed later in case the BOM changes
    after the fact.

    unit_cost_snapshot freezes the material's avg_unit_cost at build time — needed for
    the planned yield-cost feature (spreading scrapped-material cost across the
    successful units of a run) without having to reconstruct a historical average cost
    later.
    """

    __tablename__ = "build_failed_consumptions"
    __table_args__ = (
        UniqueConstraint("build_id", "material_id", name="uq_build_failed_consumptions_build_material"),
        CheckConstraint("qty_consumed >= 0", name="ck_build_failed_consumptions_qty_consumed_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id", ondelete="CASCADE"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False)
    was_consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qty_consumed: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    unit_cost_snapshot: Mapped[float | None] = mapped_column(Numeric(14, 6), nullable=True)
