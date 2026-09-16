from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderLineSubstitution(Base):
    """Audit trail + undo state for "customer wants a different variant" — always a split:
    qty units move off original_line_id onto a brand-new new_line_id pointed at the
    replacement variant, never a mutation of the original line's product_id/variant_id in
    place. See OrderLine's own docstring for why original_line_id is allowed to end up at
    ordered_qty == 0 (a whole-line substitution) rather than being deleted.

    Undoing moves new_line_id's *current* ordered_qty back onto original_line_id — not this
    row's own qty, so undo stays correct even if new_line_id was itself later split further
    by another substitution (reverted_qty then differs from qty, which is left alone as the
    historical record of what was first moved). Blocked once new_line_id has any shipped_qty
    — a substitution that's already shipped can't be un-substituted.

    Undo then deletes the now-empty new line outright when nothing else hangs off it, so the
    order doesn't accumulate blank 0-qty rows; new_line_id goes NULL here (ON DELETE SET NULL)
    and reverted_at/reverted_qty are what's left of the record. The one time the line is kept
    is when it's the original_line_id of a still-active later substitution — deleting it would
    cascade that row away and orphan its replacement line.
    """

    __tablename__ = "order_line_substitutions"
    __table_args__ = (UniqueConstraint("new_line_id", name="uq_order_line_substitutions_new_line"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    original_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False)
    new_line_id: Mapped[int | None] = mapped_column(ForeignKey("order_lines.id", ondelete="SET NULL"), nullable=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverted_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
