"""Work-flow configuration: stages, routes, the default, and which route a product resolves to.

Service-level, against the in-memory schema. The rules worth pinning are the ones a seller could
trip over: a route must have exactly one Make and end with Ship, built-ins can't be deleted, the
shop always has exactly one default, and the product > category > default fallback.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.material_category import MaterialCategory
from app.models.product import Product
from app.models.product_category import ProductCategory
from app.models.work import WorkQtyBasis, WorkRoute, WorkStage, WorkStageKind
from app.services import work_routes
from app.services.work_routes import StepInput, WorkConfigError


@pytest_asyncio.fixture
async def defaults(session):
    await work_routes.ensure_work_defaults(session)
    await session.commit()


async def _builtin(session, kind: WorkStageKind) -> WorkStage:
    return (await session.execute(select(WorkStage).where(WorkStage.kind == kind))).scalar_one()


async def _stage(session, name: str, **kw) -> WorkStage:
    return await work_routes.create_stage(
        session,
        name=name,
        station=kw.get("station"),
        per_order=kw.get("per_order", False),
        wait_minutes=kw.get("wait_minutes"),
        typical_minutes=kw.get("typical_minutes"),
    )


async def _route(session, name: str, *stages: WorkStage, **kw) -> WorkRoute:
    return await work_routes.create_route(
        session, name=name, steps=[StepInput(stage_id=s.id, **kw) for s in stages]
    )


# ---- seeding ----------------------------------------------------------------------------------


async def test_seed_creates_make_ship_and_a_default_route(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    routes = await work_routes.list_routes(session)
    assert [r.name for r in routes] == ["Standard"]
    assert routes[0].is_default
    assert [s.stage_id for s in routes[0].steps] == [make.id, ship.id]


async def test_seed_is_idempotent(session, defaults):
    await work_routes.ensure_work_defaults(session)
    await session.commit()
    assert len(await work_routes.list_stages(session)) == 2
    assert len(await work_routes.list_routes(session)) == 1


async def test_seed_does_not_resurrect_a_deleted_default_route(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    other = await _route(session, "Other", make, ship)
    await work_routes.set_default_route(session, other.id)
    standard = next(r for r in await work_routes.list_routes(session) if r.name == "Standard")
    await work_routes.delete_route(session, standard.id)
    await work_routes.ensure_work_defaults(session)
    await session.commit()
    assert [r.name for r in await work_routes.list_routes(session)] == ["Other"]


async def test_seed_promotes_a_route_when_none_is_default(session, defaults):
    route = (await work_routes.list_routes(session))[0]
    route.is_default = False
    await session.commit()
    await work_routes.ensure_work_defaults(session)
    await session.commit()
    assert (await work_routes.get_default_route(session)).id == route.id


# ---- stages -----------------------------------------------------------------------------------


async def test_stage_names_are_unique_ignoring_case(session, defaults):
    await _stage(session, "Mask")
    with pytest.raises(WorkConfigError) as exc:
        await _stage(session, "  mask ")
    assert exc.value.status == 409


async def test_a_stage_needs_a_name(session, defaults):
    with pytest.raises(WorkConfigError):
        await _stage(session, "   ")


async def test_built_in_stages_cannot_be_deleted_but_can_be_renamed(session, defaults):
    make = await _builtin(session, WorkStageKind.make)
    with pytest.raises(WorkConfigError) as exc:
        await work_routes.delete_stage(session, make.id)
    assert exc.value.status == 409
    renamed = await work_routes.update_stage(session, make.id, {"name": "Craft"})
    assert renamed.name == "Craft" and renamed.kind == WorkStageKind.make


async def test_built_in_stages_cannot_be_per_order(session, defaults):
    make = await _builtin(session, WorkStageKind.make)
    with pytest.raises(WorkConfigError):
        await work_routes.update_stage(session, make.id, {"per_order": True})


async def test_update_only_touches_the_fields_sent(session, defaults):
    stage = await _stage(session, "Sand", station="Bench", wait_minutes=30)
    await work_routes.update_stage(session, stage.id, {"station": None})
    refreshed = await work_routes.get_stage(session, stage.id)
    assert refreshed.station is None and refreshed.wait_minutes == 30 and refreshed.name == "Sand"


async def test_a_stage_in_a_route_cannot_be_deleted(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    await _route(session, "Coaster", mask, make, ship)
    with pytest.raises(WorkConfigError) as exc:
        await work_routes.delete_stage(session, mask.id)
    assert "route" in str(exc.value)


async def test_an_unused_stage_can_be_deleted(session, defaults):
    stage = await _stage(session, "Sand")
    await work_routes.delete_stage(session, stage.id)
    assert await session.get(WorkStage, stage.id) is None


async def test_stages_list_built_ins_first(session, defaults):
    await _stage(session, "Aaa first alphabetically")
    names = [s.name for s in await work_routes.list_stages(session)]
    assert names[:2] == ["Make", "Ship"]


# ---- route validation -------------------------------------------------------------------------


async def test_route_needs_make_and_ship(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    for stages in ([mask, ship], [mask, make], [make, ship, mask], [ship, make]):
        with pytest.raises(WorkConfigError):
            await _route(session, "Bad", *stages)


async def test_route_rejects_a_repeated_stage(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    with pytest.raises(WorkConfigError):
        await _route(session, "Bad", mask, mask, make, ship)


async def test_route_names_are_unique(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    await _route(session, "Coaster", make, ship)
    with pytest.raises(WorkConfigError) as exc:
        await _route(session, "coaster", make, ship)
    assert exc.value.status == 409


async def test_qty_basis_by_category_needs_a_category(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    with pytest.raises(WorkConfigError):
        await work_routes.create_route(
            session,
            name="Coaster",
            steps=[
                StepInput(stage_id=mask.id, qty_basis=WorkQtyBasis.bom_category),
                StepInput(stage_id=make.id),
                StepInput(stage_id=ship.id),
            ],
        )


async def test_qty_basis_by_category_round_trips(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    route = await work_routes.create_route(
        session,
        name="Coaster",
        steps=[
            StepInput(stage_id=mask.id, qty_basis=WorkQtyBasis.bom_category, qty_category_id=category.id, mix_variants=True),
            StepInput(stage_id=make.id),
            StepInput(stage_id=ship.id),
        ],
    )
    first = route.steps[0]
    assert first.qty_basis == WorkQtyBasis.bom_category
    assert first.qty_category_id == category.id and first.mix_variants
    assert [s.position for s in route.steps] == [1, 2, 3]


async def test_a_category_without_that_basis_is_rejected(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    with pytest.raises(WorkConfigError):
        await work_routes.create_route(
            session,
            name="Bad",
            steps=[StepInput(stage_id=make.id, qty_category_id=category.id), StepInput(stage_id=ship.id)],
        )


async def test_built_in_steps_cannot_count_by_category(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    category = (await session.execute(select(MaterialCategory).limit(1))).scalar_one()
    with pytest.raises(WorkConfigError):
        await work_routes.create_route(
            session,
            name="Bad",
            steps=[
                StepInput(stage_id=make.id, qty_basis=WorkQtyBasis.bom_category, qty_category_id=category.id),
                StepInput(stage_id=ship.id),
            ],
        )


async def test_update_route_replaces_steps_and_reorders(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    clean, mask = await _stage(session, "Pre-clean"), await _stage(session, "Mask")
    route = await _route(session, "Coaster", clean, mask, make, ship)
    updated = await work_routes.update_route(
        session,
        route.id,
        name=None,
        steps=[StepInput(stage_id=mask.id), StepInput(stage_id=clean.id), StepInput(stage_id=make.id), StepInput(stage_id=ship.id)],
    )
    assert [s.stage.name for s in updated.steps] == ["Mask", "Pre-clean", "Make", "Ship"]


async def test_update_route_rejects_an_invalid_list_and_keeps_the_old_one(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    mask = await _stage(session, "Mask")
    route = await _route(session, "Coaster", mask, make, ship)
    with pytest.raises(WorkConfigError):
        await work_routes.update_route(session, route.id, name=None, steps=[StepInput(stage_id=mask.id)])
    assert [s.stage.name for s in (await work_routes.get_route(session, route.id)).steps] == ["Mask", "Make", "Ship"]


# ---- default ----------------------------------------------------------------------------------


async def test_exactly_one_default_and_it_cannot_be_deleted(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    other = await _route(session, "Other", make, ship)
    standard = next(r for r in await work_routes.list_routes(session) if r.name == "Standard")
    with pytest.raises(WorkConfigError) as exc:
        await work_routes.delete_route(session, standard.id)
    assert exc.value.status == 409
    await work_routes.set_default_route(session, other.id)
    defaults_now = [r.name for r in await work_routes.list_routes(session) if r.is_default]
    assert defaults_now == ["Other"]


# ---- resolution -------------------------------------------------------------------------------


async def test_resolution_is_product_then_category_then_default(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    cat_route = await _route(session, "Category route", make, ship)
    own_route = await _route(session, "Own route", make, ship)

    category = ProductCategory(name="Coasters", work_route_id=cat_route.id)
    session.add(category)
    await session.flush()
    inherits = Product(name="Inherits", sku="A", product_category_id=category.id)
    own = Product(name="Own", sku="B", product_category_id=category.id, work_route_id=own_route.id)
    uncategorised = Product(name="Plain", sku="C")
    session.add_all([inherits, own, uncategorised])
    await session.commit()

    resolved = await work_routes.resolve_routes(session)
    assert resolved[inherits.id].name == "Category route"
    assert resolved[own.id].name == "Own route"
    assert resolved[uncategorised.id].name == "Standard"
    assert (await work_routes.resolve_route(session, own.id)).id == own_route.id


async def test_a_route_in_use_cannot_be_deleted(session, defaults):
    make, ship = await _builtin(session, WorkStageKind.make), await _builtin(session, WorkStageKind.ship)
    route = await _route(session, "Coaster", make, ship)
    category = ProductCategory(name="Coasters", work_route_id=route.id)
    product = Product(name="P", sku="P", work_route_id=route.id)
    session.add_all([category, product])
    await session.commit()
    assert (await work_routes.route_usage(session))[route.id] == (1, 1)
    with pytest.raises(WorkConfigError) as exc:
        await work_routes.delete_route(session, route.id)
    assert "1 category" in str(exc.value) and "1 product" in str(exc.value)


async def test_require_route_exists(session, defaults):
    await work_routes.require_route_exists(session, None)
    with pytest.raises(WorkConfigError) as exc:
        await work_routes.require_route_exists(session, 9999)
    assert exc.value.status == 404


# ---- the switch -------------------------------------------------------------------------------


async def test_enabling_stamps_the_first_time_and_never_moves_it(session, defaults):
    settings = await work_routes.get_work_settings(session)
    assert settings.work_flow_enabled is False and settings.work_flow_enabled_at is None
    on = await work_routes.set_work_flow_enabled(session, True)
    stamp = on.work_flow_enabled_at
    assert on.work_flow_enabled and stamp is not None
    off = await work_routes.set_work_flow_enabled(session, False)
    assert off.work_flow_enabled is False and off.work_flow_enabled_at == stamp
    again = await work_routes.set_work_flow_enabled(session, True)
    assert again.work_flow_enabled_at == stamp
