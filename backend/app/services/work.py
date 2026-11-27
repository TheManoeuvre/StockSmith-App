"""The Work list: what to do next, derived from orders, builds, stock and recorded progress.

See docs/plan-workflow.md. **Nothing here is stored except progress events** — every card is
recomputed from the current state of the shop, so there is no work-list table to drift out of step
with the orders it describes.

How "what's outstanding" is worked out, for one product/variant (a *key*, K):

* **Demand** is the sum of its open order lines' shortfall (ordered − allocated − short-shipped).
  Units already in stock were allocated when the order arrived and never enter the route.
* **Made** is the number of units built since the feature was switched on (`builds`), so a build
  recorded on the ordinary build form counts exactly like one recorded from the Work page.
* **Progress at a stage** is the signed sum of that stage's events for K, in the stage's own
  units. Pooled — it is not tied to an order — which is what lets the seller prepare ahead of
  demand and what makes a cancelled order's prep reusable without special handling.
* A unit has passed stage *i* if it has completed stage *i* **or any later one** (`eff`), so ticking
  a later stage first, or building without ticking prep, never leaves ghost work behind.
* **Waiting** at a stage = units past it that are not yet made. Work still to start there is
  demand − waiting, limited to what has cleared the stage before it.
* Make is read from builds. Stages after Make are finishing: units made but not yet through them,
  capped by what is physically on hand. Ship is the existing order flow; an order is ready to ship
  when every line is allocated and enough finished stock exists to cover it.

Per-order stages ("make the file") are the exception: their progress is recorded against the order
line, and a line cannot be made until they are done.

Known simplifications, kept deliberately small for the first release (see the plan):
progress is pooled per variant rather than shared across pack sizes, and due dates come only from
the order's own ship-by/collect-by date.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build
from app.models.material import Material
from app.models.material_category import MaterialCategory
from app.models.order import Order, OrderFulfilmentMethod, OrderLine, OrderStatus
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant
from app.models.work import (
    WorkEvent,
    WorkQtyBasis,
    WorkRoute,
    WorkRouteStep,
    WorkStage,
    WorkStageKind,
)
from app.schemas.work import (
    WorkCard,
    WorkListRead,
    WorkOrderCard,
    WorkShipLine,
    WorkStationRead,
    WorkSummaryRead,
    WorkVariantPart,
)
from app.services import work_routes
from app.services.buildability import get_resolved_variant_bom
from app.services.general_settings import get_general_settings
from app.services.platforms.base import ensure_utc
from app.services.work_routes import WorkConfigError, WorkNotFoundError

Key = tuple[int, int | None]  # (product_id, variant_id)


@dataclass
class _Line:
    id: int
    order_id: int
    product_id: int
    variant_id: int | None
    product_name: str
    variant_name: str | None
    is_bundle: bool
    ordered: int
    shortfall: int  # units still to be made: ordered - allocated - short_shipped
    to_ship: int  # units allocated and not yet shipped
    due: datetime | None
    placed_at: datetime | None
    platform: str | None
    buyer_name: str | None

    @property
    def key(self) -> Key:
        return (self.product_id, self.variant_id)


@dataclass
class _Part:
    """One key's contribution to a card, before cards of the same batch are merged."""

    step: WorkRouteStep
    key: Key
    product_name: str
    variant_name: str | None
    qty: int
    finished_units: int
    due: datetime | None
    order_ids: list[int]
    unit_label: str | None
    signature: tuple[int, ...]
    order_line_id: int | None = None
    batch_note: str | None = None


@dataclass
class _State:
    """Everything loaded once per request."""

    now: datetime
    enabled_at: datetime | None
    lines: list[_Line]
    routes: dict[int, WorkRoute]
    pooled: dict[tuple[int, int | None, int], int]  # (product, variant, stage) -> signed sum
    per_line: dict[tuple[int, int], int]  # (order_line, stage) -> signed sum
    made: dict[Key, int]
    stock: dict[Key, int]
    category_of: dict[int, int | None]  # material -> category
    category_name: dict[int, str]
    colour_categories: set[int]
    ship_station: str | None
    extra_demand: dict[Key, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


async def _load_lines(session: AsyncSession) -> list[_Line]:
    rows = (
        await session.execute(
            select(OrderLine, Order, Product.name, Product.is_bundle, ProductVariant.variant_name)
            .join(Order, Order.id == OrderLine.order_id)
            .join(Product, Product.id == OrderLine.product_id)
            .outerjoin(ProductVariant, ProductVariant.id == OrderLine.variant_id)
            .where(
                OrderLine.needs_mapping.is_(False),
                OrderLine.product_id.is_not(None),
                Order.status.in_([OrderStatus.pending, OrderStatus.allocated]),
                # Same exclusions as the dashboard's "awaiting inventory" (services/buildability.py):
                # an order the marketplace says is shipped, or is cancelling, isn't work.
                Order.marketplace_shipped_at.is_(None),
                Order.pending_marketplace_cancellation.is_(False),
            )
        )
    ).all()
    lines: list[_Line] = []
    for line, order, product_name, is_bundle, variant_name in rows:
        # Collection orders are due when collected, everything else when it must ship — the same rule the
        # dashboard's awaiting-inventory list uses.
        due = order.collect_by if order.fulfilment_method == OrderFulfilmentMethod.collect else order.ship_by_date
        lines.append(
            _Line(
                id=line.id,
                order_id=line.order_id,
                product_id=line.product_id,
                variant_id=line.variant_id,
                product_name=product_name,
                variant_name=variant_name,
                is_bundle=bool(is_bundle),
                ordered=line.ordered_qty,
                shortfall=max(0, line.ordered_qty - line.allocated_qty - line.short_shipped_qty),
                to_ship=max(0, line.allocated_qty - line.shipped_qty),
                due=ensure_utc(due) if due is not None else None,
                placed_at=ensure_utc(order.order_placed_at) if order.order_placed_at is not None else None,
                platform=order.platform.value if order.platform is not None else None,
                buyer_name=order.buyer_name,
            )
        )
    return lines


async def _load_state(session: AsyncSession, now: datetime, extra_demand: dict[Key, int] | None) -> _State:
    settings = await get_general_settings(session)
    enabled_at = ensure_utc(settings.work_flow_enabled_at) if settings.work_flow_enabled_at else None
    lines = await _load_lines(session)

    pooled: dict[tuple[int, int | None, int], int] = {}
    per_line: dict[tuple[int, int], int] = {}
    for product_id, variant_id, line_id, stage_id, total in await session.execute(
        select(WorkEvent.product_id, WorkEvent.variant_id, WorkEvent.order_line_id, WorkEvent.stage_id, func.sum(WorkEvent.qty))
        .group_by(WorkEvent.product_id, WorkEvent.variant_id, WorkEvent.order_line_id, WorkEvent.stage_id)
    ):
        if line_id is None:
            pooled[(product_id, variant_id, stage_id)] = pooled.get((product_id, variant_id, stage_id), 0) + int(total)
        else:
            per_line[(line_id, stage_id)] = per_line.get((line_id, stage_id), 0) + int(total)

    made: dict[Key, int] = {}
    build_stmt = select(Build.product_id, Build.variant_id, func.sum(Build.qty_built)).group_by(Build.product_id, Build.variant_id)
    if enabled_at is not None:
        build_stmt = build_stmt.where(Build.built_at >= enabled_at)
    for product_id, variant_id, total in await session.execute(build_stmt):
        made[(product_id, variant_id)] = int(total or 0)

    stock: dict[Key, int] = {}
    for pid, cur in await session.execute(select(Product.id, Product.current_stock)):
        stock[(pid, None)] = int(cur)
    for pid, vid, cur in await session.execute(select(ProductVariant.product_id, ProductVariant.id, ProductVariant.current_stock)):
        stock[(pid, vid)] = int(cur)

    category_of = {mid: cid for mid, cid in await session.execute(select(Material.id, Material.category_id))}
    categories = list(await session.execute(select(MaterialCategory.id, MaterialCategory.name, MaterialCategory.tracks_colour)))
    ship = await work_routes._first_stage_of_kind(session, WorkStageKind.ship)

    candidate_products = {l.product_id for l in lines} | {k[0] for k in made} | {k[0] for k in (extra_demand or {})}
    candidate_products |= {p for (p, _v, _s) in pooled}
    routes = await work_routes.resolve_routes(session, sorted(candidate_products)) if candidate_products else {}

    return _State(
        now=now,
        enabled_at=enabled_at,
        lines=lines,
        routes=routes,
        pooled=pooled,
        per_line=per_line,
        made=made,
        stock=stock,
        category_of=category_of,
        category_name={cid: name for cid, name, _ in categories},
        colour_categories={cid for cid, _, colour in categories if colour},
        ship_station=ship.station if ship else None,
        extra_demand=extra_demand or {},
    )


async def _bom(session: AsyncSession, product_id: int, variant_id: int | None) -> list[tuple[int, Decimal]]:
    if variant_id is not None:
        return [(l.material_id, l.qty_required) for l in await get_resolved_variant_bom(session, product_id, variant_id)]
    rows = await session.execute(select(ProductMaterial.material_id, ProductMaterial.qty_required).where(ProductMaterial.product_id == product_id))
    return [(mid, Decimal(qty)) for mid, qty in rows]


# ---------------------------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------------------------


def _factor(step: WorkRouteStep, bom: list[tuple[int, Decimal]], state: _State) -> int:
    """Stage units per finished item. 1 unless the step counts by BOM material category, in which
    case it is the BOM's quantity from that category (six blanks in a 6-pack). A BOM with none of that
    category falls back to 1 rather than zero, so the stage still shows work."""
    if step.qty_basis != WorkQtyBasis.bom_category or step.qty_category_id is None:
        return 1
    total = sum((qty for mid, qty in bom if state.category_of.get(mid) == step.qty_category_id), Decimal(0))
    return max(1, ceil(total))


def _signature(bom: list[tuple[int, Decimal]], state: _State) -> tuple[int, ...]:
    """The materials a batch prints from: the BOM lines whose category tracks colour. Two variants
    merge into one batch only if these match, so black PETG never joins black PLA."""
    return tuple(sorted({mid for mid, _ in bom if state.category_of.get(mid) in state.colour_categories}))


def _min_due(dues: list[datetime | None]) -> datetime | None:
    real = [d for d in dues if d is not None]
    return min(real) if real else None


async def _parts_for_key(
    session: AsyncSession, state: _State, key: Key, route: WorkRoute, lines: list[_Line]
) -> tuple[list[_Part], int | None]:
    """Cards for one product/variant, and how many of its finished units are ready to pack
    (None = not limited, i.e. a bundle or no finishing steps)."""
    steps = list(route.steps)
    make_at = next(i for i, s in enumerate(steps) if s.stage.kind == WorkStageKind.make)
    pre = steps[:make_at]
    post = steps[make_at + 1 : -1]
    product_id, variant_id = key
    open_lines = sorted((l for l in lines if l.key == key and l.shortfall > 0), key=lambda l: (l.due is None, l.due, l.placed_at or state.now, l.id))
    extra = state.extra_demand.get(key, 0)
    has_progress = any(p == product_id and v == variant_id for (p, v, _s) in state.pooled)
    if not open_lines and not extra and not has_progress and not post:
        return [], None

    bom = await _bom(session, product_id, variant_id)
    signature = _signature(bom, state)
    if lines:
        product_name, variant_name = lines[0].product_name, lines[0].variant_name
    else:
        product = await session.get(Product, product_id)
        product_name = product.name if product else f"Product #{product_id}"
        if variant_id is not None:
            variant = await session.get(ProductVariant, variant_id)
            variant_name = variant.variant_name if variant else None

    made = state.made.get(key, 0)
    factor = {s.id: _factor(s, bom, state) for s in steps}

    def label(step: WorkRouteStep) -> str | None:
        if step.qty_basis == WorkQtyBasis.bom_category and step.qty_category_id is not None:
            return state.category_name.get(step.qty_category_id)
        return None

    def done(step: WorkRouteStep) -> int:
        return max(0, state.pooled.get((product_id, variant_id, step.stage_id), 0))

    def make_part(step: WorkRouteStep, qty: int, finished: int, due, order_ids, **kw) -> _Part:
        return _Part(
            step=step, key=key, product_name=product_name, variant_name=variant_name, qty=qty,
            finished_units=finished, due=due, order_ids=order_ids, unit_label=label(step), signature=signature, **kw,
        )

    # -- per-order stages: progress is recorded against the line, and gates it from being made
    per_order_steps = [s for s in pre if s.stage.per_order]
    pooled_pre = [s for s in pre if not s.stage.per_order]

    def line_done_units(line: _Line, step: WorkRouteStep) -> int:
        return max(0, state.per_line.get((line.id, step.stage_id), 0)) // factor[step.id]

    def eligible(line: _Line, before: int) -> int:
        """How much of the line's shortfall has cleared every per-order stage ahead of position `before`."""
        gates = [line_done_units(line, s) for s in per_order_steps if steps.index(s) < before]
        return min([line.shortfall, *gates])

    parts: list[_Part] = []
    for step in per_order_steps:
        for line in open_lines:
            remaining = max(0, line.shortfall * factor[step.id] - max(0, state.per_line.get((line.id, step.stage_id), 0)))
            if remaining:
                parts.append(
                    make_part(step, remaining, line.shortfall, line.due, [line.order_id], order_line_id=line.id)
                )

    # -- pooled preparation
    def demand_at(position: int) -> int:
        return sum(eligible(l, position) for l in open_lines) + extra

    def served_at(position: int) -> list[_Line]:
        """The lines whose work can actually proceed at this position — those that have cleared any
        per-order step ahead of it. A card names only these orders, not ones still waiting on a file."""
        return [l for l in open_lines if eligible(l, position) > 0]

    # eff[i]: units that have completed pooled stage i or any later one, never below what is made.
    eff: dict[int, int] = {}
    running = made
    for step in reversed(pooled_pre):
        running = max(running, done(step) // factor[step.id])
        eff[step.id] = running
    ready_for_make = None  # None = nothing to wait on
    previous_eff: int | None = None
    for step in pooled_pre:
        position = steps.index(step)
        waiting = max(0, eff[step.id] - made)
        need = max(0, demand_at(position) - waiting)
        ready = need if previous_eff is None else min(need, max(0, previous_eff - eff[step.id]))
        previous_eff = eff[step.id]
        if ready:
            u = done(step) // factor[step.id]
            partial = done(step) - u * factor[step.id] if u == eff[step.id] else 0
            qty = max(0, ready * factor[step.id] - partial)
            if qty:
                served = served_at(position)
                parts.append(make_part(step, qty, ready, _min_due([l.due for l in served]), sorted({l.order_id for l in served})))
    if pooled_pre:
        ready_for_make = max(0, eff[pooled_pre[-1].id] - made)

    # -- make
    make_step = steps[make_at]
    make_demand = demand_at(make_at)
    make_units = make_demand if ready_for_make is None else min(make_demand, ready_for_make)
    if make_units > 0:
        served = served_at(make_at)
        parts.append(make_part(make_step, make_units, make_units, _min_due([l.due for l in served]), sorted({l.order_id for l in served})))

    # -- finishing (after Make)
    ready_stock: int | None = None
    if post:
        post_eff: dict[int, int] = {}
        running = 0
        for step in reversed(post):
            running = max(running, done(step) // factor[step.id])
            post_eff[step.id] = running
        on_hand = state.stock.get(key, 0)
        upstream = made
        previous = made
        for index, step in enumerate(post):
            backlog = max(0, previous - post_eff[step.id])
            if index == 0:
                backlog = min(backlog, on_hand)  # cannot finish more than physically exists
            previous = post_eff[step.id]
            if backlog:
                u = done(step) // factor[step.id]
                partial = done(step) - u * factor[step.id] if u == post_eff[step.id] else 0
                qty = max(0, backlog * factor[step.id] - partial)
                if qty:
                    served = sorted({l.order_id for l in lines if l.key == key and l.to_ship > 0})
                    parts.append(make_part(step, qty, backlog, _min_due([l.due for l in lines if l.key == key and l.to_ship > 0]), served))
        outstanding = min(max(0, upstream - post_eff[post[-1].id]), on_hand)
        ready_stock = max(0, on_hand - outstanding)
    return parts, ready_stock


def _merge(parts: list[_Part], state: _State) -> list[WorkCard]:
    """Same stage, same product, and — only where the route step allows mixing — same materials."""
    groups: dict[tuple, list[_Part]] = {}
    for part in parts:
        if part.order_line_id is not None:
            group = ("line", part.step.stage_id, part.order_line_id)
        elif part.step.mix_variants:
            group = ("mix", part.step.stage_id, part.key[0], part.signature)
        else:
            group = ("one", part.step.stage_id, part.key)
        groups.setdefault(group, []).append(part)

    cards: list[WorkCard] = []
    for group, members in groups.items():
        first = members[0]
        stage = first.step.stage
        dues = [m.due for m in members]
        due = _min_due(dues)
        order_ids = sorted({o for m in members for o in m.order_ids})
        key = ":".join(str(x) for x in (*group[:3], *([",".join(map(str, group[3]))] if group[0] == "mix" else [])))
        cards.append(
            WorkCard(
                key=f"stage:{key}",
                stage_id=stage.id,
                stage_name=stage.name,
                stage_kind=stage.kind,
                station=stage.station,
                product_id=first.key[0],
                product_name=first.product_name,
                parts=[
                    WorkVariantPart(variant_id=m.key[1], variant_name=m.variant_name, qty=m.qty, finished_units=m.finished_units)
                    for m in sorted(members, key=lambda m: (m.due is None, m.due, m.variant_name or ""))
                ],
                qty=sum(m.qty for m in members),
                unit_label=first.unit_label,
                due=due,
                late=due is not None and due < state.now,
                order_ids=order_ids,
                order_line_id=first.order_line_id,
                can_record=stage.kind == WorkStageKind.custom,
                batch_note=("Mixed batch" if len(members) > 1 else None),
            )
        )
    cards.sort(key=lambda c: (c.due is None, c.due or state.now, c.product_name, c.stage_name))
    return cards


def _order_cards(lines: list[_Line], ready_stock: dict[Key, int | None], state: _State) -> tuple[list[WorkOrderCard], list[WorkOrderCard]]:
    """Which open orders can be packed now, and which are still waiting on something."""
    by_order: dict[int, list[_Line]] = {}
    for line in lines:
        by_order.setdefault(line.order_id, []).append(line)

    remaining = {k: v for k, v in ready_stock.items()}
    ordered = sorted(by_order.values(), key=lambda ls: (min((l.due for l in ls if l.due), default=None) is None, min((l.due for l in ls if l.due), default=state.now), ls[0].order_id))
    ready_cards: list[WorkOrderCard] = []
    waiting_cards: list[WorkOrderCard] = []
    for order_lines in ordered:
        shippable = [l for l in order_lines if l.shortfall > 0 or l.to_ship > 0]
        if not shippable:
            continue
        shipped_lines: list[WorkShipLine] = []
        waiting_for: list[str] = []
        claim: dict[Key, int] = {}
        for line in shippable:
            name = line.product_name + (f" — {line.variant_name}" if line.variant_name else "")
            if line.shortfall > 0:
                ok, reason = False, f"{name}: still to make"
            elif line.is_bundle or remaining.get(line.key) is None:
                ok, reason = True, ""
            elif remaining[line.key] - claim.get(line.key, 0) >= line.to_ship:
                ok, reason = True, ""
                claim[line.key] = claim.get(line.key, 0) + line.to_ship
            else:
                ok, reason = False, f"{name}: still to finish"
            shipped_lines.append(
                WorkShipLine(product_id=line.product_id, product_name=line.product_name, variant_name=line.variant_name, qty=max(line.to_ship, line.shortfall), ready=ok)
            )
            if not ok:
                waiting_for.append(reason)
        ready = not waiting_for
        if ready:
            for k, n in claim.items():
                remaining[k] = remaining[k] - n
        due = min((l.due for l in order_lines if l.due), default=None)
        first = order_lines[0]
        card = WorkOrderCard(
            order_id=first.order_id,
            platform=first.platform,
            buyer_name=first.buyer_name,
            due=due,
            late=due is not None and due < state.now,
            lines=shipped_lines,
            ready_lines=sum(1 for l in shipped_lines if l.ready),
            total_lines=len(shipped_lines),
            ready=ready,
            waiting_for=waiting_for,
        )
        (ready_cards if ready else waiting_cards).append(card)
    return ready_cards, waiting_cards


async def compute_work_list(
    session: AsyncSession, *, now: datetime | None = None, extra_demand: dict[Key, int] | None = None
) -> WorkListRead:
    """The whole Work page in one call. `extra_demand` is the hook for stock top-ups (phase 3)."""
    now = now or datetime.now(timezone.utc)
    settings = await get_general_settings(session)
    if not settings.work_flow_enabled:
        return WorkListRead(enabled=False, generated_at=now, stations=[], cards=[], ship_cards=[], waiting_orders=[])

    state = await _load_state(session, now, extra_demand)
    keys: set[Key] = {l.key for l in state.lines if not l.is_bundle}
    keys |= {(p, v) for (p, v, _s) in state.pooled}
    keys |= set(state.made)
    keys |= set(state.extra_demand)

    parts: list[_Part] = []
    ready_stock: dict[Key, int | None] = {}
    lines_by_key: dict[Key, list[_Line]] = {}
    for line in state.lines:
        lines_by_key.setdefault(line.key, []).append(line)
    for key in sorted(keys, key=lambda k: (k[0], k[1] or 0)):
        route = state.routes.get(key[0])
        if route is None:
            continue
        key_parts, ready = await _parts_for_key(session, state, key, route, lines_by_key.get(key, []))
        parts.extend(key_parts)
        ready_stock[key] = ready  # None = unlimited

    cards = _merge(parts, state)
    ship_cards, waiting = _order_cards(state.lines, ready_stock, state)

    stations: dict[str | None, list[datetime | None]] = {}
    for card in cards:
        stations.setdefault(card.station, []).append(card.due)
    if ship_cards:
        stations.setdefault(state.ship_station, []).extend(c.due for c in ship_cards)
    station_reads = [
        WorkStationRead(name=name, count=len(dues), earliest_due=_min_due(dues))
        for name, dues in stations.items()
    ]
    station_reads.sort(key=lambda s: (s.earliest_due is None, s.earliest_due or now, s.name or ""))
    return WorkListRead(
        enabled=True,
        generated_at=now,
        stations=station_reads,
        ship_station=state.ship_station,
        cards=cards,
        ship_cards=ship_cards,
        waiting_orders=waiting,
    )


async def compute_work_summary(session: AsyncSession, *, now: datetime | None = None) -> WorkSummaryRead:
    now = now or datetime.now(timezone.utc)
    work = await compute_work_list(session, now=now)
    if not work.enabled:
        return WorkSummaryRead(enabled=False, late=0, due_today=0, open_cards=0, ready_to_ship=0)
    items = [c.due for c in work.cards] + [c.due for c in work.ship_cards]
    return WorkSummaryRead(
        enabled=True,
        late=sum(1 for d in items if d is not None and d < now),
        due_today=sum(1 for d in items if d is not None and d >= now and d.date() == now.date()),
        open_cards=len(work.cards) + len(work.ship_cards),
        ready_to_ship=len(work.ship_cards),
    )


# ---------------------------------------------------------------------------------------------
# Recording progress
# ---------------------------------------------------------------------------------------------


async def _has_active_variants(session: AsyncSession, product_id: int) -> bool:
    return (
        await session.execute(
            select(ProductVariant.id).where(ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)).limit(1)
        )
    ).first() is not None


async def _recorded(session: AsyncSession, product_id: int, variant_id: int | None, stage_id: int, order_line_id: int | None) -> int:
    stmt = select(func.coalesce(func.sum(WorkEvent.qty), 0)).where(WorkEvent.product_id == product_id, WorkEvent.stage_id == stage_id)
    stmt = stmt.where(WorkEvent.variant_id.is_(None) if variant_id is None else WorkEvent.variant_id == variant_id)
    stmt = stmt.where(WorkEvent.order_line_id.is_(None) if order_line_id is None else WorkEvent.order_line_id == order_line_id)
    return int((await session.execute(stmt)).scalar_one())


async def record_progress(
    session: AsyncSession,
    *,
    product_id: int,
    variant_id: int | None,
    stage_id: int,
    qty: int,
    order_line_id: int | None = None,
    note: str | None = None,
) -> list[WorkEvent]:
    """Record that `qty` units completed a stage (negative to take some back). Returns the events written.

    Only custom stages take progress — Make is a build and Ship is the order. With several variants
    in play and none named, the quantity is spread over the variants the card covers, most urgent
    first, which is what lets one tick on a mixed batch land correctly.
    """
    if qty == 0:
        raise WorkConfigError("Enter how many were done.")
    stage = await session.get(WorkStage, stage_id)
    if stage is None:
        raise WorkNotFoundError("Stage")
    if stage.kind != WorkStageKind.custom:
        raise WorkConfigError(
            "Making is recorded as a build, and shipping happens on the order — neither is ticked off here."
        )
    product = await session.get(Product, product_id)
    if product is None:
        raise WorkNotFoundError("Product")
    route = await work_routes.resolve_route(session, product_id)
    if stage_id not in {s.stage_id for s in route.steps}:
        raise WorkConfigError(f'"{stage.name}" is not a step in the route this product follows.')

    if stage.per_order:
        if order_line_id is None:
            raise WorkConfigError(f'"{stage.name}" is done for one order at a time — say which.')
        line = await session.get(OrderLine, order_line_id)
        if line is None or line.product_id != product_id:
            raise WorkNotFoundError("Order line")
        variant_id = line.variant_id
    elif order_line_id is not None:
        raise WorkConfigError(f'"{stage.name}" is not tied to a single order.')

    has_variants = await _has_active_variants(session, product_id)
    if variant_id is not None and not has_variants:
        raise WorkConfigError("This product has no active variants — leave the variant out.")

    allocations: list[tuple[int | None, int]]
    if variant_id is None and has_variants:
        if qty < 0:
            raise WorkConfigError("Say which variant to take progress back from.")
        allocations = await _spread_over_variants(session, product_id, stage_id, qty)
    else:
        allocations = [(variant_id, qty)]

    written: list[WorkEvent] = []
    for vid, amount in allocations:
        if amount < 0:
            have = await _recorded(session, product_id, vid, stage_id, order_line_id)
            if have + amount < 0:
                raise WorkConfigError(f"Only {have} recorded for that step, so {-amount} can't be taken back.")
        event = WorkEvent(product_id=product_id, variant_id=vid, order_line_id=order_line_id, stage_id=stage_id, qty=amount, note=note)
        session.add(event)
        written.append(event)
    await session.commit()
    for event in written:
        await session.refresh(event)
    return written


async def _spread_over_variants(session: AsyncSession, product_id: int, stage_id: int, qty: int) -> list[tuple[int | None, int]]:
    """Split `qty` across the variants that currently have work at this stage, most urgent first, each
    capped at what it needs. Anything left over is prepared ahead against the most urgent variant."""
    work = await compute_work_list(session)
    wanted: list[tuple[int | None, int]] = []
    for card in work.cards:
        if card.product_id == product_id and card.stage_id == stage_id and card.order_line_id is None:
            wanted.extend((p.variant_id, p.qty) for p in card.parts)
    if not wanted:
        variant = (
            await session.execute(
                select(ProductVariant.id).where(ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)).order_by(ProductVariant.id).limit(1)
            )
        ).scalar_one()
        return [(variant, qty)]
    remaining = qty
    out: list[tuple[int | None, int]] = []
    for vid, need in wanted:
        if remaining <= 0:
            break
        take = min(remaining, need)
        out.append((vid, take))
        remaining -= take
    if remaining > 0:
        first_vid = wanted[0][0]
        out = [(v, n + remaining) if v == first_vid else (v, n) for v, n in out]
    return out


async def undo_event(session: AsyncSession, event_id: int) -> WorkEvent:
    """Reverse an event by writing its opposite — the ledger is append-only."""
    original = await session.get(WorkEvent, event_id)
    if original is None:
        raise WorkNotFoundError("Progress entry")
    have = await _recorded(session, original.product_id, original.variant_id, original.stage_id, original.order_line_id)
    if have - original.qty < 0:
        raise WorkConfigError("That entry has already been taken back.")
    reverse = WorkEvent(
        product_id=original.product_id,
        variant_id=original.variant_id,
        order_line_id=original.order_line_id,
        stage_id=original.stage_id,
        qty=-original.qty,
        note=f"Undo of #{original.id}",
    )
    session.add(reverse)
    await session.commit()
    await session.refresh(reverse)
    return reverse


async def recent_events(session: AsyncSession, limit: int = 20) -> list[tuple[WorkEvent, str, str, str | None]]:
    rows = await session.execute(
        select(WorkEvent, WorkStage.name, Product.name, ProductVariant.variant_name)
        .join(WorkStage, WorkStage.id == WorkEvent.stage_id)
        .join(Product, Product.id == WorkEvent.product_id)
        .outerjoin(ProductVariant, ProductVariant.id == WorkEvent.variant_id)
        .order_by(WorkEvent.id.desc())
        .limit(limit)
    )
    return [(e, s, p, v) for e, s, p, v in rows]
