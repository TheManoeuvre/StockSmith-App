"""Keeps single-placement items' location quantities in step with their stock automatically.

The rule (docs/plan-locations.md §5.1): an item placed in exactly one location follows its stock —
a decrease is taken off that placement (never below zero), an increase is added to it. An item
with no placement is left alone. One kept in several places has decreases taken from its
primary first, then the fullest others; increases stay Unassigned until put away.

This watches the quantity itself rather than patching every code path that changes it, so a new
caller is covered without anyone remembering to. Materials are caught in
costing.recompute_material (the one place current_qty is derived, including after edits to old
records); products and variants are caught by the before_flush listener below, because
current_stock is assigned directly in many places.

Deliberately imports only models, so app.models can register the listener without a cycle.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import event, select
from sqlalchemy.orm import Session, attributes

from app.models.location import PlacementEvent, PlacementEventKind, StockPlacement
from app.models.product import Product
from app.models.variant import ProductVariant


# Switched on while a location count applies, which sets the placement itself and writes its own
# history row; following the stock as well would record the same change twice.
_following_paused: ContextVar[bool] = ContextVar("placement_following_paused", default=False)


# Which place a decrease should come from when the item is kept in several: set by the request
# that knows (a person picked "Taking from" on a form), read when the stock change is applied.
_preferred_source: ContextVar[int | None] = ContextVar("placement_preferred_source", default=None)


@contextmanager
def taking_from(location_id: int | None):
    token = _preferred_source.set(location_id)
    try:
        yield
    finally:
        _preferred_source.reset(token)


@contextmanager
def following_paused():
    token = _following_paused.set(True)
    try:
        yield
    finally:
        _following_paused.reset(token)


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


def _deduct(session: Session, item: ItemRef, placement: StockPlacement, amount: Decimal, reason: str | None) -> None:
    placement.qty = Decimal(placement.qty or 0) - amount
    session.add(
        PlacementEvent(
            **item.event_fields(),
            from_location_id=placement.location_id,
            qty=amount,
            kind=PlacementEventKind.auto_deduct,
            reason=reason,
        )
    )


def _take_order(placements: list[StockPlacement]) -> list[StockPlacement]:
    """Where a decrease with nobody at the screen comes from: the preferred place if one was
    named, then the primary, then whatever holds the most (docs/plan-locations.md §5.2)."""
    preferred = _preferred_source.get()
    return sorted(
        placements,
        key=lambda r: (r.location_id != preferred, not r.is_primary, -Decimal(r.qty or 0), r.id),
    )


def apply_stock_delta(session: Session, item: ItemRef, delta: Decimal, reason: str | None = None) -> None:
    """Applies a change in an item's total to where it is kept.

    - One placement: it follows the stock both ways.
    - Several: a decrease is taken from the preferred place if one was named, then the primary,
      then the fullest others, never below zero. An increase is left Unassigned for the user to
      put away, because there is no way to know which place a delivery is going to.
    - None: left alone.

    Takes the plain (sync) Session so it can run inside a flush; async callers reach it through
    ``await session.run_sync(...)``. Never commits.
    """
    if delta == 0 or _following_paused.get():
        return
    placements = session.execute(select(StockPlacement).where(*item.owner_filters())).scalars().all()
    if not placements:
        return
    if delta < 0:
        # Clamp: stock that was never placed (Unassigned) is the rest of the decrease.
        remaining = -delta
        for placement in _take_order(list(placements)):
            if remaining <= 0:
                break
            take = min(remaining, Decimal(placement.qty or 0))
            if take <= 0:
                continue
            _deduct(session, item, placement, take, reason)
            remaining -= take
    elif len(placements) == 1:
        placement = placements[0]
        placement.qty = Decimal(placement.qty or 0) + delta
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
