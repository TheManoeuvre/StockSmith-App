"""Keeps single-placement items' location quantities in step with their stock automatically.

The rule (docs/plan-locations.md §5.1): an item placed in exactly one location follows its stock —
a decrease is taken off that placement (never below zero), an increase is added to it. An item
with no placement, or with several, is left alone; several is the drift-warning case until the
phase 4 prompt.

This watches the quantity itself rather than patching every code path that changes it, so a new
caller is covered without anyone remembering to. Materials are caught in
costing.recompute_material (the one place current_qty is derived, including after edits to old
records); products and variants are caught by the before_flush listener below, because
current_stock is assigned directly in many places.

Deliberately imports only models, so app.models can register the listener without a cycle.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import event, select
from sqlalchemy.orm import Session, attributes

from app.models.location import PlacementEvent, PlacementEventKind, StockPlacement
from app.models.product import Product
from app.models.variant import ProductVariant


@dataclass(frozen=True)
class ItemRef:
    """What a placement belongs to: a material, or a product with an optional variant."""

    material_id: int | None = None
    product_id: int | None = None
    variant_id: int | None = None

    def owner_filters(self):
        if self.material_id is not None:
            return (StockPlacement.material_id == self.material_id,)
        if self.variant_id is not None:
            return (StockPlacement.product_id == self.product_id, StockPlacement.variant_id == self.variant_id)
        return (StockPlacement.product_id == self.product_id, StockPlacement.variant_id.is_(None))

    def event_fields(self) -> dict:
        return {"material_id": self.material_id, "product_id": self.product_id, "variant_id": self.variant_id}


def apply_stock_delta(session: Session, item: ItemRef, delta: Decimal, reason: str | None = None) -> None:
    """Applies a change in an item's total to its single placement, if it has exactly one.

    Takes the plain (sync) Session so it can run inside a flush; async callers reach it through
    ``await session.run_sync(...)``. Never commits.
    """
    if delta == 0:
        return
    placements = session.execute(select(StockPlacement).where(*item.owner_filters())).scalars().all()
    if len(placements) != 1:
        return
    placement = placements[0]
    current = Decimal(placement.qty or 0)
    if delta < 0:
        # Clamp: stock that was never placed (Unassigned) is the rest of the decrease.
        take = min(-delta, current)
        if take <= 0:
            return
        placement.qty = current - take
        session.add(
            PlacementEvent(
                **item.event_fields(),
                from_location_id=placement.location_id,
                qty=take,
                kind=PlacementEventKind.auto_deduct,
                reason=reason,
            )
        )
    else:
        placement.qty = current + delta
        session.add(
            PlacementEvent(
                **item.event_fields(),
                to_location_id=placement.location_id,
                qty=delta,
                kind=PlacementEventKind.auto_add,
                reason=reason,
            )
        )


def _reason(delta: Decimal) -> str:
    return "Stock went down" if delta < 0 else "Stock went up"


@event.listens_for(Session, "before_flush")
def _follow_product_stock(session: Session, flush_context, instances) -> None:
    for obj in list(session.dirty):
        if not isinstance(obj, (Product, ProductVariant)):
            continue
        history = attributes.get_history(obj, "current_stock", passive=attributes.PASSIVE_NO_INITIALIZE)
        if not history.has_changes() or not history.deleted or history.deleted[0] is None:
            continue
        delta = Decimal(int(obj.current_stock) - int(history.deleted[0]))
        if delta == 0:
            continue
        if isinstance(obj, ProductVariant):
            item = ItemRef(product_id=obj.product_id, variant_id=obj.id)
        else:
            item = ItemRef(product_id=obj.id)
        apply_stock_delta(session, item, delta, _reason(delta))
