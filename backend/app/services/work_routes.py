"""Work-flow configuration: the stage library, routes, and which route a product follows.

Resolution mirrors ABC classification (services/abc.py) and, like it, belongs in exactly one place:

    route = product.work_route_id ?? product_category.work_route_id ?? the shop default route

NULL means "inherit", never "unset". A route is valid when it contains **exactly one Make stage and
exactly one Ship stage, with Ship last** — Make is where a `Build` is recorded and Ship is where the
order flow takes over, so a route without both has nothing to connect to. Stages before Make are
preparation, stages between Make and Ship are finishing.

Nothing here knows about orders or progress; that is services/work.py.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.general_settings import GeneralSettings
from app.models.material_category import MaterialCategory
from app.models.product import Product
from app.models.product_category import ProductCategory
from app.services.general_settings import get_general_settings
from app.models.work import (
    WorkEvent,
    WorkQtyBasis,
    WorkRoute,
    WorkRouteStep,
    WorkStage,
    WorkStageKind,
)

# Starter names. Generic on purpose — StockSmith is not specific to one craft — and the seller can
# rename every one of them. Stations are suggestions, not a fixed list.
BUILT_IN_MAKE = {"name": "Make", "station": "Making"}
BUILT_IN_SHIP = {"name": "Ship", "station": "Packing"}
DEFAULT_ROUTE_NAME = "Standard"


class WorkConfigError(RuntimeError):
    """Refused. The message is user-facing. `status` is the HTTP code the router should use."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class WorkNotFoundError(WorkConfigError):
    def __init__(self, what: str):
        super().__init__(f"{what} not found.", 404)


@dataclass(frozen=True)
class StepInput:
    stage_id: int
    mix_variants: bool = False
    qty_basis: WorkQtyBasis = WorkQtyBasis.unit
    qty_category_id: int | None = None


# ---------------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------------


async def ensure_work_defaults(session: AsyncSession) -> None:
    """Create the two built-in stages and a default route, if missing. Idempotent; called on every
    startup from seed.ensure_seed_data.

    Guarded on what is *missing*, not on the table being empty, for the built-ins — they are not
    deletable, so "missing" can only mean "never created". The default route is different: it is
    editable and deletable (once another is the default), so it is only created into a database
    with no routes at all, and otherwise a default is *ensured* by promoting the oldest route.
    """
    make = await _first_stage_of_kind(session, WorkStageKind.make)
    if make is None:
        make = WorkStage(kind=WorkStageKind.make, **BUILT_IN_MAKE)
        session.add(make)
    ship = await _first_stage_of_kind(session, WorkStageKind.ship)
    if ship is None:
        ship = WorkStage(kind=WorkStageKind.ship, **BUILT_IN_SHIP)
        session.add(ship)
    await session.flush()

    routes = list((await session.execute(select(WorkRoute).order_by(WorkRoute.id))).scalars())
    if not routes:
        route = WorkRoute(name=DEFAULT_ROUTE_NAME, is_default=True)
        route.steps = [
            WorkRouteStep(stage_id=make.id, position=1),
            WorkRouteStep(stage_id=ship.id, position=2),
        ]
        session.add(route)
    elif not any(r.is_default for r in routes):
        routes[0].is_default = True


async def _first_stage_of_kind(session: AsyncSession, kind: WorkStageKind) -> WorkStage | None:
    return (
        await session.execute(select(WorkStage).where(WorkStage.kind == kind).order_by(WorkStage.id).limit(1))
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------------------------


def _clean_name(name: str | None, what: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise WorkConfigError(f"{what} needs a name.")
    return cleaned


async def _name_taken(session: AsyncSession, model, name: str, *, except_id: int | None = None) -> bool:
    stmt = select(model.id).where(func.lower(model.name) == name.lower())
    if except_id is not None:
        stmt = stmt.where(model.id != except_id)
    return (await session.execute(stmt.limit(1))).first() is not None


async def get_stage(session: AsyncSession, stage_id: int) -> WorkStage:
    stage = await session.get(WorkStage, stage_id)
    if stage is None:
        raise WorkNotFoundError("Stage")
    return stage


async def list_stages(session: AsyncSession) -> list[WorkStage]:
    """Built-ins first (Make, then Ship), then the seller's own by name — the order the library reads in."""
    stages = list((await session.execute(select(WorkStage))).scalars())
    rank = {WorkStageKind.make: 0, WorkStageKind.ship: 1, WorkStageKind.custom: 2}
    return sorted(stages, key=lambda s: (rank[s.kind], s.name.lower()))


async def stage_usage(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """stage id -> (routes using it, progress events recorded against it)."""
    routes = {
        sid: n
        for sid, n in (
            await session.execute(
                select(WorkRouteStep.stage_id, func.count(func.distinct(WorkRouteStep.route_id))).group_by(
                    WorkRouteStep.stage_id
                )
            )
        )
    }
    events = {
        sid: n
        for sid, n in (await session.execute(select(WorkEvent.stage_id, func.count()).group_by(WorkEvent.stage_id)))
    }
    return {sid: (routes.get(sid, 0), events.get(sid, 0)) for sid in set(routes) | set(events)}


async def create_stage(
    session: AsyncSession,
    *,
    name: str,
    station: str | None,
    per_order: bool,
    wait_minutes: int | None,
    typical_minutes: int | None,
) -> WorkStage:
    name = _clean_name(name, "A stage")
    if await _name_taken(session, WorkStage, name):
        raise WorkConfigError(f'There is already a stage called "{name}".', 409)
    stage = WorkStage(
        name=name,
        station=(station or "").strip() or None,
        kind=WorkStageKind.custom,
        per_order=per_order,
        wait_minutes=wait_minutes,
        typical_minutes=typical_minutes,
    )
    session.add(stage)
    await session.commit()
    await session.refresh(stage)
    return stage


async def update_stage(session: AsyncSession, stage_id: int, fields: dict) -> WorkStage:
    """Apply only the keys present in `fields` — "not mentioned" and "set to null" differ."""
    stage = await get_stage(session, stage_id)
    if "name" in fields:
        name = _clean_name(fields["name"], "A stage")
        if await _name_taken(session, WorkStage, name, except_id=stage_id):
            raise WorkConfigError(f'There is already a stage called "{name}".', 409)
        stage.name = name
    if "station" in fields:
        stage.station = (fields["station"] or "").strip() or None
    if "per_order" in fields:
        if fields["per_order"] and stage.kind != WorkStageKind.custom:
            raise WorkConfigError(f'"{stage.name}" is a built-in step and cannot be tied to a single order.')
        stage.per_order = bool(fields["per_order"])
    for key in ("wait_minutes", "typical_minutes"):
        if key in fields:
            setattr(stage, key, fields[key])
    await session.commit()
    await session.refresh(stage)
    return stage


async def delete_stage(session: AsyncSession, stage_id: int) -> None:
    stage = await get_stage(session, stage_id)
    if stage.kind != WorkStageKind.custom:
        raise WorkConfigError(f'"{stage.name}" is built in and cannot be deleted. You can rename it.', 409)
    routes, events = (await stage_usage(session)).get(stage_id, (0, 0))
    if routes:
        raise WorkConfigError(
            f'"{stage.name}" is used by {routes} route{"s" if routes != 1 else ""}. Remove it from them first.', 409
        )
    if events:
        # Progress recorded against it would be orphaned. The events are history of real work, so the
        # stage is kept rather than silently cascading them away.
        raise WorkConfigError(f'"{stage.name}" has recorded progress, so it is kept. Take it out of every route instead.', 409)
    await session.delete(stage)
    await session.commit()


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------


def _route_query():
    return select(WorkRoute).options(selectinload(WorkRoute.steps).selectinload(WorkRouteStep.stage))


async def list_routes(session: AsyncSession) -> list[WorkRoute]:
    return list((await session.execute(_route_query().order_by(WorkRoute.name))).scalars())


async def get_route(session: AsyncSession, route_id: int) -> WorkRoute:
    route = (await session.execute(_route_query().where(WorkRoute.id == route_id))).scalar_one_or_none()
    if route is None:
        raise WorkNotFoundError("Route")
    return route


async def route_usage(session: AsyncSession) -> dict[int, tuple[int, int]]:
    """route id -> (products naming it, categories naming it)."""
    products = {
        rid: n
        for rid, n in (
            await session.execute(
                select(Product.work_route_id, func.count()).where(Product.work_route_id.is_not(None)).group_by(Product.work_route_id)
            )
        )
    }
    categories = {
        rid: n
        for rid, n in (
            await session.execute(
                select(ProductCategory.work_route_id, func.count())
                .where(ProductCategory.work_route_id.is_not(None))
                .group_by(ProductCategory.work_route_id)
            )
        )
    }
    return {rid: (products.get(rid, 0), categories.get(rid, 0)) for rid in set(products) | set(categories)}


async def _validate_steps(session: AsyncSession, steps: list[StepInput]) -> list[WorkStage]:
    """Check a proposed step list and return the stages in order."""
    if len(steps) < 2:
        raise WorkConfigError("A route needs at least a Make step and a Ship step.")
    stage_ids = [s.stage_id for s in steps]
    if len(set(stage_ids)) != len(stage_ids):
        raise WorkConfigError("A stage can appear in a route only once.")
    stages = {
        s.id: s for s in (await session.execute(select(WorkStage).where(WorkStage.id.in_(stage_ids)))).scalars()
    }
    missing = [sid for sid in stage_ids if sid not in stages]
    if missing:
        raise WorkConfigError("One of the stages in this route no longer exists.", 404)
    ordered = [stages[sid] for sid in stage_ids]

    makes = [s for s in ordered if s.kind == WorkStageKind.make]
    ships = [s for s in ordered if s.kind == WorkStageKind.ship]
    if len(makes) != 1:
        raise WorkConfigError("A route needs exactly one Make step.")
    if len(ships) != 1:
        raise WorkConfigError("A route needs exactly one Ship step.")
    if ordered[-1].kind != WorkStageKind.ship:
        raise WorkConfigError("Ship has to be the last step in a route.")

    make_at = next(i for i, s in enumerate(ordered) if s.kind == WorkStageKind.make)
    for i, stage in enumerate(ordered):
        if stage.per_order and i > make_at:
            # Work tied to one buyer is something you do *before* making (a personalised file).
            # After Make the items are pooled stock, so there is no order to tie it to.
            raise WorkConfigError(f'"{stage.name}" is tied to a single order, so it has to come before Make.')

    category_ids = {s.qty_category_id for s in steps if s.qty_category_id is not None}
    if category_ids:
        found = set(
            (await session.execute(select(MaterialCategory.id).where(MaterialCategory.id.in_(category_ids)))).scalars()
        )
        if category_ids - found:
            raise WorkConfigError("A material category chosen for counting no longer exists.", 404)
    for step, stage in zip(steps, ordered):
        if step.qty_basis == WorkQtyBasis.bom_category:
            if step.qty_category_id is None:
                raise WorkConfigError(f'Choose which material category "{stage.name}" counts.')
            if stage.kind != WorkStageKind.custom:
                raise WorkConfigError(f'"{stage.name}" is built in and always counts finished items.')
        elif step.qty_category_id is not None:
            raise WorkConfigError(f'"{stage.name}" names a material category but counts finished items.')
    return ordered


def _step_rows(steps: list[StepInput]) -> list[WorkRouteStep]:
    return [
        WorkRouteStep(
            stage_id=s.stage_id,
            position=i,
            mix_variants=s.mix_variants,
            qty_basis=s.qty_basis,
            qty_category_id=s.qty_category_id if s.qty_basis == WorkQtyBasis.bom_category else None,
        )
        for i, s in enumerate(steps, start=1)
    ]


async def create_route(session: AsyncSession, *, name: str, steps: list[StepInput]) -> WorkRoute:
    name = _clean_name(name, "A route")
    if await _name_taken(session, WorkRoute, name):
        raise WorkConfigError(f'There is already a route called "{name}".', 409)
    await _validate_steps(session, steps)
    route = WorkRoute(name=name, is_default=False)
    route.steps = _step_rows(steps)
    session.add(route)
    await session.commit()
    return await get_route(session, route.id)


async def update_route(
    session: AsyncSession, route_id: int, *, name: str | None, steps: list[StepInput] | None
) -> WorkRoute:
    route = await get_route(session, route_id)
    if name is not None:
        name = _clean_name(name, "A route")
        if await _name_taken(session, WorkRoute, name, except_id=route_id):
            raise WorkConfigError(f'There is already a route called "{name}".', 409)
        route.name = name
    if steps is not None:
        await _validate_steps(session, steps)
        # Replace wholesale. Order is the point of a route, and the unique (route, position) key
        # would trip over an in-place shuffle, so the old rows go in the same flush as the new ones.
        route.steps.clear()
        await session.flush()
        route.steps.extend(_step_rows(steps))
    await session.commit()
    session.expire_all()
    return await get_route(session, route_id)


async def set_default_route(session: AsyncSession, route_id: int) -> WorkRoute:
    route = await get_route(session, route_id)
    if route.is_default:
        return route
    # Clear before set: the partial unique index forbids two defaults even inside one flush.
    for other in (await session.execute(select(WorkRoute).where(WorkRoute.is_default))).scalars():
        other.is_default = False
    await session.flush()
    route.is_default = True
    await session.commit()
    session.expire_all()
    return await get_route(session, route_id)


async def delete_route(session: AsyncSession, route_id: int) -> None:
    route = await get_route(session, route_id)
    if route.is_default:
        raise WorkConfigError(f'"{route.name}" is the shop default. Make another route the default first.', 409)
    products, categories = (await route_usage(session)).get(route_id, (0, 0))
    if products or categories:
        parts = []
        if categories:
            parts.append(f"{categories} categor{'y' if categories == 1 else 'ies'}")
        if products:
            parts.append(f"{products} product{'s' if products != 1 else ''}")
        raise WorkConfigError(f'"{route.name}" is still used by {" and ".join(parts)}. Point them at another route first.', 409)
    await session.delete(route)
    await session.commit()


# ---------------------------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------------------------


async def get_default_route(session: AsyncSession) -> WorkRoute:
    route = (await session.execute(_route_query().where(WorkRoute.is_default))).scalar_one_or_none()
    if route is None:
        # Only reachable on a database the seed has not touched. Create rather than fail: every
        # product must resolve to something.
        await ensure_work_defaults(session)
        await session.commit()
        route = (await session.execute(_route_query().where(WorkRoute.is_default))).scalar_one()
    return route


async def resolve_routes(session: AsyncSession, product_ids: list[int] | None = None) -> dict[int, WorkRoute]:
    """product id -> the route it follows. One pass over products, categories and routes.

    The three-level fallback is written once, here; callers resolving many products must use this
    rather than looping `resolve_route`, which would be N+1 on the Work page.
    """
    routes = {r.id: r for r in await list_routes(session)}
    default = await get_default_route(session)
    category_route = {
        cid: rid
        for cid, rid in (
            await session.execute(
                select(ProductCategory.id, ProductCategory.work_route_id).where(ProductCategory.work_route_id.is_not(None))
            )
        )
    }
    stmt = select(Product.id, Product.work_route_id, Product.product_category_id)
    if product_ids is not None:
        stmt = stmt.where(Product.id.in_(product_ids))
    resolved: dict[int, WorkRoute] = {}
    for pid, own, category_id in await session.execute(stmt):
        rid = own if own is not None else category_route.get(category_id)
        resolved[pid] = routes.get(rid, default) if rid is not None else default
    return resolved


async def resolve_route(session: AsyncSession, product_id: int) -> WorkRoute:
    routes = await resolve_routes(session, [product_id])
    if product_id not in routes:
        raise WorkNotFoundError("Product")
    return routes[product_id]


async def require_route_exists(session: AsyncSession, route_id: int | None) -> None:
    """For writers of Product.work_route_id / ProductCategory.work_route_id: a clear 404 rather than a
    foreign-key error."""
    if route_id is not None and await session.get(WorkRoute, route_id) is None:
        raise WorkNotFoundError("Route")


# ---------------------------------------------------------------------------------------------
# The on/off switch
# ---------------------------------------------------------------------------------------------


async def get_work_settings(session: AsyncSession) -> GeneralSettings:
    return await get_general_settings(session)


async def set_work_flow_enabled(session: AsyncSession, enabled: bool) -> GeneralSettings:
    """Switch the feature on or off. `work_flow_enabled_at` records the *first* time it was switched
    on and is never cleared: it bounds which builds count as "made" (services/work.py), and
    moving it on a later re-enable would make work already counted reappear as outstanding."""
    settings = await get_general_settings(session)
    settings.work_flow_enabled = enabled
    if enabled and settings.work_flow_enabled_at is None:
        settings.work_flow_enabled_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(settings)
    return settings
