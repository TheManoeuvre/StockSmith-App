"""Work flow: stage library, routes, and the on/off switch.

The Work list itself (what to do next) is added to this router in the second half of the feature;
see docs/plan-workflow.md.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.work import WorkRoute, WorkRouteStep, WorkStage
from app.schemas.work import (
    WorkRouteCreate,
    WorkRouteRead,
    WorkRouteUpdate,
    WorkSettingsRead,
    WorkSettingsUpdate,
    WorkStageCreate,
    WorkStageRead,
    WorkStageUpdate,
)
from app.services import work_routes
from app.services.work_routes import StepInput, WorkConfigError

router = APIRouter(prefix="/work", tags=["work"], dependencies=[Depends(require_auth)])


def _http(exc: WorkConfigError) -> HTTPException:
    # A plain-string detail on purpose: api/client.ts turns `detail` straight into the message the
    # user reads, so a structured object would arrive as "[object Object]".
    return HTTPException(status_code=exc.status, detail=str(exc))


# ---- settings -------------------------------------------------------------------------------


@router.get("/settings", response_model=WorkSettingsRead)
async def get_work_settings(session: AsyncSession = Depends(get_db)) -> WorkSettingsRead:
    return await work_routes.get_work_settings(session)


@router.patch("/settings", response_model=WorkSettingsRead)
async def update_work_settings(payload: WorkSettingsUpdate, session: AsyncSession = Depends(get_db)) -> WorkSettingsRead:
    return await work_routes.set_work_flow_enabled(session, payload.work_flow_enabled)


# ---- stages ---------------------------------------------------------------------------------


def _stage_read(stage: WorkStage, usage: dict[int, tuple[int, int]]) -> WorkStageRead:
    routes, events = usage.get(stage.id, (0, 0))
    read = WorkStageRead.model_validate(stage)
    read.route_count = routes
    read.event_count = events
    return read


@router.get("/stages", response_model=list[WorkStageRead])
async def list_stages(session: AsyncSession = Depends(get_db)) -> list[WorkStageRead]:
    stages = await work_routes.list_stages(session)
    usage = await work_routes.stage_usage(session)
    return [_stage_read(s, usage) for s in stages]


@router.post("/stages", response_model=WorkStageRead, status_code=status.HTTP_201_CREATED)
async def create_stage(payload: WorkStageCreate, session: AsyncSession = Depends(get_db)) -> WorkStageRead:
    try:
        stage = await work_routes.create_stage(session, **payload.model_dump())
    except WorkConfigError as exc:
        raise _http(exc) from exc
    return _stage_read(stage, {})


@router.patch("/stages/{stage_id}", response_model=WorkStageRead)
async def update_stage(stage_id: int, payload: WorkStageUpdate, session: AsyncSession = Depends(get_db)) -> WorkStageRead:
    try:
        stage = await work_routes.update_stage(session, stage_id, payload.model_dump(exclude_unset=True))
    except WorkConfigError as exc:
        raise _http(exc) from exc
    return _stage_read(stage, await work_routes.stage_usage(session))


@router.delete("/stages/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stage(stage_id: int, session: AsyncSession = Depends(get_db)) -> None:
    try:
        await work_routes.delete_stage(session, stage_id)
    except WorkConfigError as exc:
        raise _http(exc) from exc


# ---- routes ---------------------------------------------------------------------------------


def _route_read(route: WorkRoute, usage: dict[int, tuple[int, int]]) -> WorkRouteRead:
    products, categories = usage.get(route.id, (0, 0))
    return WorkRouteRead(
        id=route.id,
        name=route.name,
        is_default=route.is_default,
        created_at=route.created_at,
        steps=[_step_read(step) for step in route.steps],
        product_count=products,
        category_count=categories,
    )


def _step_read(step: WorkRouteStep):
    from app.schemas.work import WorkRouteStepRead

    return WorkRouteStepRead(
        id=step.id,
        stage_id=step.stage_id,
        position=step.position,
        mix_variants=step.mix_variants,
        qty_basis=step.qty_basis,
        qty_category_id=step.qty_category_id,
        stage_name=step.stage.name,
        stage_kind=step.stage.kind,
        station=step.stage.station,
        per_order=step.stage.per_order,
    )


def _inputs(steps) -> list[StepInput]:
    return [
        StepInput(
            stage_id=s.stage_id,
            mix_variants=s.mix_variants,
            qty_basis=s.qty_basis,
            qty_category_id=s.qty_category_id,
        )
        for s in steps
    ]


@router.get("/routes", response_model=list[WorkRouteRead])
async def list_routes(session: AsyncSession = Depends(get_db)) -> list[WorkRouteRead]:
    routes = await work_routes.list_routes(session)
    usage = await work_routes.route_usage(session)
    return [_route_read(r, usage) for r in routes]


@router.post("/routes", response_model=WorkRouteRead, status_code=status.HTTP_201_CREATED)
async def create_route(payload: WorkRouteCreate, session: AsyncSession = Depends(get_db)) -> WorkRouteRead:
    try:
        route = await work_routes.create_route(session, name=payload.name, steps=_inputs(payload.steps))
    except WorkConfigError as exc:
        raise _http(exc) from exc
    return _route_read(route, {})


@router.patch("/routes/{route_id}", response_model=WorkRouteRead)
async def update_route(route_id: int, payload: WorkRouteUpdate, session: AsyncSession = Depends(get_db)) -> WorkRouteRead:
    try:
        route = await work_routes.update_route(
            session,
            route_id,
            name=payload.name,
            steps=_inputs(payload.steps) if payload.steps is not None else None,
        )
    except WorkConfigError as exc:
        raise _http(exc) from exc
    return _route_read(route, await work_routes.route_usage(session))


@router.post("/routes/{route_id}/make-default", response_model=WorkRouteRead)
async def make_default_route(route_id: int, session: AsyncSession = Depends(get_db)) -> WorkRouteRead:
    try:
        route = await work_routes.set_default_route(session, route_id)
    except WorkConfigError as exc:
        raise _http(exc) from exc
    return _route_read(route, await work_routes.route_usage(session))


@router.delete("/routes/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(route_id: int, session: AsyncSession = Depends(get_db)) -> None:
    try:
        await work_routes.delete_route(session, route_id)
    except WorkConfigError as exc:
        raise _http(exc) from exc
