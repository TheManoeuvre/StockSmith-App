"""Placing stock in locations, moving it, and reading where an item is kept
(docs/plan-locations.md §3, §4.2, §5).

Nothing here changes an item's total — placements sit beside the stock ledger. The automatic
follow-the-stock behaviour for single-placement items is in services/placement_hooks.py.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location, PlacementEvent, PlacementEventKind, StockPlacement
from app.models.material import Material
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import locations as locs
from app.services.placement_hooks import ItemRef
from app.services.validation import validate_qty_for_unit

ZERO = Decimal(0)


def _bad(detail: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


# --- Items ----------------------------------------------------------------------------------


@dataclass
class ItemInfo:
    ref: ItemRef
    name: str
    sku: str | None
    unit: str
    total: Decimal
    whole_numbers: bool


async def resolve_item(session: AsyncSession, ref: ItemRef) -> ItemInfo:
    """Checks the reference is well-formed and returns the item's name, unit and stock total."""
    if ref.material_id is not None:
        if ref.product_id is not None or ref.variant_id is not None:
            raise _bad("Choose either a material or a product, not both.")
        material = await session.get(Material, ref.material_id)
        if material is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Material not found")
        return ItemInfo(ref, material.name, None, material.unit, Decimal(material.current_qty or 0), False)
    if ref.product_id is None:
        raise _bad("Choose a material or a product to place.")
    product = await session.get(Product, ref.product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    if ref.variant_id is not None:
        variant = await session.get(ProductVariant, ref.variant_id)
        if variant is None or variant.product_id != product.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
        return ItemInfo(ref, f"{product.name} — {variant.variant_name}", product.sku, "each", Decimal(variant.current_stock), True)
    has_variants = (
        await session.scalar(
            select(ProductVariant.id)
            .where(ProductVariant.product_id == product.id, ProductVariant.is_active.is_(True))
            .limit(1)
        )
    ) is not None
    if has_variants:
        raise _bad("This product has variants, which hold the stock. Place each variant instead.")
    return ItemInfo(ref, product.name, product.sku, "each", Decimal(product.current_stock), True)


async def _validate_qty(session: AsyncSession, item: ItemInfo, qty: Decimal) -> None:
    if qty <= 0:
        raise _bad("Enter a quantity above zero.")
    if item.whole_numbers:
        if qty != qty.to_integral_value():
            raise _bad("This item is counted in whole units.")
    else:
        await validate_qty_for_unit(session, qty, item.unit, "qty")


async def item_placements(session: AsyncSession, ref: ItemRef) -> list[StockPlacement]:
    return list(
        (
            await session.scalars(
                select(StockPlacement).where(*ref.owner_filters()).order_by(StockPlacement.id)
            )
        ).all()
    )


def _placed(rows: list[StockPlacement]) -> Decimal:
    return sum((Decimal(r.qty or 0) for r in rows), ZERO)


async def _ensure_primary(session: AsyncSession, ref: ItemRef) -> None:
    """Every placed item has exactly one primary: keep the existing one, else promote the
    placement holding the most."""
    rows = await item_placements(session, ref)
    if not rows or any(r.is_primary for r in rows):
        return
    max(rows, key=lambda r: (Decimal(r.qty or 0), -r.id)).is_primary = True


def _event(ref: ItemRef, kind: PlacementEventKind, qty: Decimal, *, from_id: int | None = None, to_id: int | None = None, reason: str | None = None) -> PlacementEvent:
    return PlacementEvent(
        **ref.event_fields(), from_location_id=from_id, to_location_id=to_id, qty=qty, kind=kind, reason=reason
    )


# --- Reading --------------------------------------------------------------------------------


@dataclass
class PlacementView:
    id: int
    location_id: int
    location_path: list[str]
    qty: Decimal
    is_primary: bool


@dataclass
class ItemStoredView:
    item: ItemInfo
    placements: list[PlacementView]
    placed: Decimal
    unassigned: Decimal
    drift: Decimal  # how much more is placed than exists; 0 when healthy
    follows_stock: bool  # exactly one placement: stock changes update it automatically


async def stored_in(session: AsyncSession, ref: ItemRef) -> ItemStoredView:
    item = await resolve_item(session, ref)
    rows = await item_placements(session, ref)
    nodes = await locs.load_nodes(session)
    placed = _placed(rows)
    views = [
        PlacementView(r.id, r.location_id, locs.path_names(nodes, r.location_id), Decimal(r.qty or 0), r.is_primary)
        for r in sorted(rows, key=lambda r: (not r.is_primary, r.id))
    ]
    return ItemStoredView(
        item=item,
        placements=views,
        placed=placed,
        unassigned=max(item.total - placed, ZERO),
        drift=max(placed - item.total, ZERO),
        follows_stock=len(rows) == 1,
    )


# --- Placing and moving ---------------------------------------------------------------------


async def _live_location(session: AsyncSession, location_id: int) -> Location:
    location = await locs.get_location(session, location_id)
    if location.archived_at is not None:
        raise _bad("That location is archived. Unarchive it first, or choose another.")
    return location


async def place(session: AsyncSession, ref: ItemRef, location_id: int, qty: Decimal, *, commit: bool = True) -> StockPlacement:
    """Records that ``qty`` of the item is in the location. Limited to the item's Unassigned
    quantity: you can't place stock that is already placed, or that doesn't exist."""
    item = await resolve_item(session, ref)
    await _validate_qty(session, item, qty)
    await _live_location(session, location_id)
    rows = await item_placements(session, ref)
    unassigned = item.total - _placed(rows)
    if qty > unassigned:
        raise _bad(
            f"Only {_fmt(max(unassigned, ZERO))} of {item.name} is unassigned, so you can’t place {_fmt(qty)}."
        )
    existing = next((r for r in rows if r.location_id == location_id), None)
    if existing is not None:
        existing.qty = Decimal(existing.qty or 0) + qty
        placement = existing
    else:
        placement = StockPlacement(location_id=location_id, qty=qty, is_primary=not rows, **ref.event_fields())
        session.add(placement)
    session.add(_event(ref, PlacementEventKind.place, qty, to_id=location_id))
    await session.flush()
    if commit:
        await session.commit()
        await session.refresh(placement)
    return placement


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text


async def move_item(
    session: AsyncSession, ref: ItemRef, from_location_id: int, to_location_id: int, qty: Decimal, *, commit: bool = True
) -> None:
    """Moves part (or all) of one item's stock between two locations. The item's total never changes."""
    if from_location_id == to_location_id:
        raise _bad("Choose a different location to move it to.")
    item = await resolve_item(session, ref)
    await _validate_qty(session, item, qty)
    await _live_location(session, to_location_id)
    rows = await item_placements(session, ref)
    source = next((r for r in rows if r.location_id == from_location_id), None)
    if source is None or Decimal(source.qty or 0) < qty:
        have = Decimal(source.qty or 0) if source is not None else ZERO
        raise _bad(f"Only {_fmt(have)} of {item.name} is in that location.")
    dest = next((r for r in rows if r.location_id == to_location_id), None)

    source.qty = Decimal(source.qty) - qty
    was_primary = source.is_primary
    if dest is None:
        dest = StockPlacement(location_id=to_location_id, qty=qty, is_primary=False, **ref.event_fields())
        session.add(dest)
    else:
        dest.qty = Decimal(dest.qty or 0) + qty
    session.add(_event(ref, PlacementEventKind.move, qty, from_id=from_location_id, to_id=to_location_id))
    if source.qty == 0:
        await session.delete(source)
        # Flush the delete before promoting: the one-primary-per-item index would otherwise
        # see two primaries for an instant.
        await session.flush()
        if was_primary:
            dest.is_primary = True
    await session.flush()
    await _ensure_primary(session, ref)
    if commit:
        await session.commit()


async def empty_into(session: AsyncSession, from_location_id: int, to_location_id: int) -> int:
    """Moves everything in one location into another, merging items already there. Returns how
    many items moved. The source stays, empty."""
    if from_location_id == to_location_id:
        raise _bad("Choose a different location to move everything into.")
    await locs.get_location(session, from_location_id)
    await _live_location(session, to_location_id)
    rows = list(
        (await session.scalars(select(StockPlacement).where(StockPlacement.location_id == from_location_id))).all()
    )
    for row in rows:
        ref = ItemRef(material_id=row.material_id, product_id=row.product_id, variant_id=row.variant_id)
        qty = Decimal(row.qty or 0)
        if qty > 0:
            await move_item(session, ref, from_location_id, to_location_id, qty, commit=False)
        else:
            was_primary = row.is_primary
            await session.delete(row)
            await session.flush()
            if was_primary:
                await _ensure_primary(session, ref)
    await session.commit()
    return len(rows)


async def make_primary(session: AsyncSession, placement_id: int) -> None:
    placement = await session.get(StockPlacement, placement_id)
    if placement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found")
    ref = ItemRef(placement.material_id, placement.product_id, placement.variant_id)
    for row in await item_placements(session, ref):
        row.is_primary = False
    await session.flush()  # clears the old primary before setting the new, for the partial unique index
    placement.is_primary = True
    await session.commit()


async def reduce_placement(session: AsyncSession, placement_id: int, qty: Decimal) -> None:
    """Takes ``qty`` off a placement without moving it anywhere — the one-click fix for drift."""
    placement = await session.get(StockPlacement, placement_id)
    if placement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found")
    ref = ItemRef(placement.material_id, placement.product_id, placement.variant_id)
    item = await resolve_item(session, ref)
    await _validate_qty(session, item, qty)
    if qty > Decimal(placement.qty or 0):
        raise _bad(f"Only {_fmt(Decimal(placement.qty or 0))} is placed there.")
    placement.qty = Decimal(placement.qty) - qty
    session.add(_event(ref, PlacementEventKind.remove, qty, from_id=placement.location_id, reason="Corrected after drift"))
    was_primary = placement.is_primary
    if placement.qty == 0:
        await session.delete(placement)
    await session.flush()
    if was_primary:
        await _ensure_primary(session, ref)
    await session.commit()


async def remove_placement(session: AsyncSession, placement_id: int) -> None:
    """Un-places an item from a location: its stock goes back to Unassigned."""
    placement = await session.get(StockPlacement, placement_id)
    if placement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Placement not found")
    ref = ItemRef(placement.material_id, placement.product_id, placement.variant_id)
    session.add(_event(ref, PlacementEventKind.remove, Decimal(placement.qty or 0), from_id=placement.location_id))
    was_primary = placement.is_primary
    await session.delete(placement)
    await session.flush()
    if was_primary:
        await _ensure_primary(session, ref)
    await session.commit()


# --- Whole-system views ---------------------------------------------------------------------


@dataclass
class PlaceableItem:
    ref: ItemRef
    name: str
    sku: str | None
    unit: str
    total: Decimal


async def placeable_items(session: AsyncSession) -> list[PlaceableItem]:
    """Everything that can be stored: active materials, plus finished products — each variant of
    a product that has variants, the product itself otherwise. Bundles are left out because their
    stock is derived from their components."""
    items: list[PlaceableItem] = []
    for m in (await session.scalars(select(Material).where(Material.is_active.is_(True)))).all():
        items.append(PlaceableItem(ItemRef(material_id=m.id), m.name, None, m.unit, Decimal(m.current_qty or 0)))
    variants = (
        await session.execute(
            select(ProductVariant, Product)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.is_active.is_(True), Product.is_active.is_(True), Product.is_bundle.is_(False))
        )
    ).all()
    with_variants = {p.id for _, p in variants}
    for v, p in variants:
        items.append(
            PlaceableItem(
                ItemRef(product_id=p.id, variant_id=v.id), f"{p.name} — {v.variant_name}", p.sku, "each", Decimal(v.current_stock)
            )
        )
    for p in (
        await session.scalars(select(Product).where(Product.is_active.is_(True), Product.is_bundle.is_(False)))
    ).all():
        if p.id not in with_variants:
            items.append(PlaceableItem(ItemRef(product_id=p.id), p.name, p.sku, "each", Decimal(p.current_stock)))
    return items


def _key(ref: ItemRef) -> tuple:
    return (ref.material_id, ref.product_id, ref.variant_id)


@dataclass
class ItemSummary:
    ref: ItemRef
    name: str
    sku: str | None
    unit: str
    primary_path: list[str] | None
    extra_places: int
    placed: Decimal
    total: Decimal
    drift: Decimal


async def summaries(session: AsyncSession) -> dict[tuple, ItemSummary]:
    """One row per placeable item: where it is (primary location, how many others) and whether it
    has drifted. Feeds the Location column and filters on the Materials and Products lists."""
    items = {_key(i.ref): i for i in await placeable_items(session)}
    nodes = await locs.load_nodes(session)
    by_item: dict[tuple, list[StockPlacement]] = {}
    for row in (await session.scalars(select(StockPlacement))).all():
        by_item.setdefault((row.material_id, row.product_id, row.variant_id), []).append(row)
    out: dict[tuple, ItemSummary] = {}
    for key, item in items.items():
        rows = by_item.get(key, [])
        primary = next((r for r in rows if r.is_primary), rows[0] if rows else None)
        placed = _placed(rows)
        out[key] = ItemSummary(
            item.ref,
            item.name,
            item.sku,
            item.unit,
            locs.path_names(nodes, primary.location_id) if primary else None,
            max(len(rows) - 1, 0),
            placed,
            item.total,
            max(placed - item.total, ZERO),
        )
    return out


@dataclass
class LocationRow:
    node: locs.LocationNode
    own_items: int
    rolled_up_items: int
    needs_attention: bool


@dataclass
class TreeSummary:
    rows: list[LocationRow]
    unplaced_items: int
    attention_locations: int
    empty_locations: int
    total_locations: int
    placeable_items: int


async def tree_summary(session: AsyncSession) -> TreeSummary:
    nodes = await locs.load_nodes(session)
    sums = await summaries(session)
    drifting = {k for k, s in sums.items() if s.drift > 0}
    own: dict[int, int] = {}
    attention: set[int] = set()
    for row in (await session.scalars(select(StockPlacement))).all():
        own[row.location_id] = own.get(row.location_id, 0) + 1
        if (row.material_id, row.product_id, row.variant_id) in drifting:
            attention.add(row.location_id)
    # Roll counts and attention up to every ancestor, so a collapsed branch still tells you
    # what's inside it.
    rolled: dict[int, int] = {}
    attention_up: set[int] = set()
    for loc_id, count in own.items():
        for anc in locs.path_ids(nodes, loc_id):
            rolled[anc] = rolled.get(anc, 0) + count
    for loc_id in attention:
        attention_up.update(locs.path_ids(nodes, loc_id))
    rows = [
        LocationRow(n, own.get(n.id, 0), rolled.get(n.id, 0), n.id in attention_up)
        for n in sorted(nodes.values(), key=lambda n: (n.parent_id or 0, _natural(n.name), n.id))
    ]
    live = [r for r in rows if not r.node.archived]
    unplaced = sum(1 for s in sums.values() if s.primary_path is None and s.total > 0)
    return TreeSummary(
        rows=rows,
        unplaced_items=unplaced,
        attention_locations=sum(1 for r in live if r.needs_attention),
        empty_locations=sum(1 for r in live if r.rolled_up_items == 0),
        total_locations=len(live),
        placeable_items=len(sums),
    )


def _natural(name: str) -> list:
    """Natural sort key, so Box2 comes before Box10."""
    import re

    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


@dataclass
class ContentRow:
    placement_id: int
    ref: ItemRef
    name: str
    sku: str | None
    unit: str
    qty: Decimal
    is_primary: bool
    drift: Decimal


@dataclass
class LocationDetail:
    node: locs.LocationNode
    path: list[tuple[int, str]]
    contents: list[ContentRow]
    children: list[LocationRow]
    history: list[PlacementEvent]
    history_count: int
    item_names: dict[tuple, str] = field(default_factory=dict)
    location_paths: dict[int, str] = field(default_factory=dict)


async def location_detail(session: AsyncSession, location_id: int, history_limit: int = 50) -> LocationDetail:
    location = await locs.get_location(session, location_id)
    nodes = await locs.load_nodes(session)
    node = nodes[location.id]
    tree = await tree_summary(session)
    items = {_key(i.ref): i for i in await placeable_items(session)}
    sums = await summaries(session)

    contents: list[ContentRow] = []
    for row in (
        await session.scalars(select(StockPlacement).where(StockPlacement.location_id == location_id))
    ).all():
        key = (row.material_id, row.product_id, row.variant_id)
        info = items.get(key)
        if info is None:  # inactive or since-bundled item that still has a placement
            try:
                resolved = await resolve_item(session, ItemRef(*key))
            except HTTPException:
                continue
            info = PlaceableItem(resolved.ref, resolved.name, resolved.sku, resolved.unit, resolved.total)
        s = sums.get(key)
        contents.append(
            ContentRow(
                row.id, info.ref, info.name, info.sku, info.unit, Decimal(row.qty or 0), row.is_primary, s.drift if s else ZERO
            )
        )
    contents.sort(key=lambda c: c.name.lower())

    history_filter = (PlacementEvent.from_location_id == location_id) | (PlacementEvent.to_location_id == location_id)
    history = list(
        (
            await session.scalars(
                select(PlacementEvent).where(history_filter).order_by(PlacementEvent.id.desc()).limit(history_limit)
            )
        ).all()
    )
    history_count = await session.scalar(select(func.count()).select_from(PlacementEvent).where(history_filter)) or 0
    detail = LocationDetail(
        node=node,
        path=[(i, nodes[i].name) for i in locs.path_ids(nodes, location_id)],
        contents=contents,
        children=[r for r in tree.rows if r.node.parent_id == location_id],
        history=history,
        history_count=history_count,
    )
    # Names for history rows: items and the locations on either end.
    for ev in history:
        key = (ev.material_id, ev.product_id, ev.variant_id)
        if key not in detail.item_names:
            info = items.get(key)
            if info is None:
                try:
                    info_name = (await resolve_item(session, ItemRef(*key))).name
                except HTTPException:
                    info_name = "Removed item"
            else:
                info_name = info.name
            detail.item_names[key] = info_name
        for lid in (ev.from_location_id, ev.to_location_id):
            if lid is not None and lid in nodes:
                detail.location_paths[lid] = locs.display_path(nodes, lid)
    return detail


async def delete_owner_placements(session: AsyncSession, ref: ItemRef) -> None:
    await session.execute(delete(StockPlacement).where(*ref.owner_filters()))
