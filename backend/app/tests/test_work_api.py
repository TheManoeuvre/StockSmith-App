"""The work-flow HTTP surface: status codes and messages for the cases a settings screen will hit, and
the route fields on products and product categories.

A real app over a throwaway file-backed database — same arrangement as test_maintenance_mode, for the
same reason (the setup and the requests run on different event loops).
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.deps import get_db, require_auth
from app.main import app
from app.models.base import Base
from app.models.general_settings import CurrencyCode, GeneralSettings
from app.services import work_routes


@pytest.fixture
def api(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'work-api.db').as_posix()}"

    async def _setup() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                s.add(GeneralSettings(id=1, default_currency=CurrencyCode.GBP))
                await s.flush()
                await work_routes.ensure_work_defaults(s)
                await s.commit()
        finally:
            await engine.dispose()

    asyncio.run(_setup())
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = lambda: None
    yield TestClient(app, client=("127.0.0.1", 51234))
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def _stage_id(api, kind):
    return next(s["id"] for s in api.get("/api/v1/work/stages").json() if s["kind"] == kind)


def test_a_fresh_install_has_make_ship_and_a_default_route_and_is_off(api):
    stages = api.get("/api/v1/work/stages").json()
    assert [s["name"] for s in stages] == ["Make", "Ship"]
    routes = api.get("/api/v1/work/routes").json()
    assert [(r["name"], r["is_default"], [s["stage_name"] for s in r["steps"]]) for r in routes] == [("Standard", True, ["Make", "Ship"])]
    assert api.get("/api/v1/work/settings").json() == {"work_flow_enabled": False, "work_flow_enabled_at": None}


def test_switching_on_and_the_empty_work_list(api):
    on = api.patch("/api/v1/work/settings", json={"work_flow_enabled": True}).json()
    assert on["work_flow_enabled"] is True and on["work_flow_enabled_at"]
    listing = api.get("/api/v1/work/list").json()
    assert listing["enabled"] is True and listing["cards"] == [] and listing["ship_cards"] == []
    assert api.get("/api/v1/work/summary").json()["open_cards"] == 0


def test_stage_crud_and_the_messages_a_user_sees(api):
    created = api.post("/api/v1/work/stages", json={"name": "Mask", "station": "Bench"})
    assert created.status_code == 201 and created.json()["kind"] == "custom"
    clash = api.post("/api/v1/work/stages", json={"name": "mask"})
    assert clash.status_code == 409 and "already a stage called" in clash.json()["detail"]
    builtin = api.delete(f"/api/v1/work/stages/{_stage_id(api, 'make')}")
    assert builtin.status_code == 409 and "built in" in builtin.json()["detail"]
    patched = api.patch(f"/api/v1/work/stages/{created.json()['id']}", json={"wait_minutes": 20})
    assert patched.json()["wait_minutes"] == 20 and patched.json()["station"] == "Bench"
    assert api.delete(f"/api/v1/work/stages/{created.json()['id']}").status_code == 204
    assert api.patch("/api/v1/work/stages/9999", json={"name": "x"}).status_code == 404


def test_route_validation_and_default_rules(api):
    make, ship = _stage_id(api, "make"), _stage_id(api, "ship")
    bad = api.post("/api/v1/work/routes", json={"name": "Bad", "steps": [{"stage_id": ship}, {"stage_id": make}]})
    assert bad.status_code == 400 and "last step" in bad.json()["detail"]
    ok = api.post("/api/v1/work/routes", json={"name": "Second", "steps": [{"stage_id": make}, {"stage_id": ship}]})
    assert ok.status_code == 201 and ok.json()["is_default"] is False
    standard = next(r for r in api.get("/api/v1/work/routes").json() if r["name"] == "Standard")
    refused = api.delete(f"/api/v1/work/routes/{standard['id']}")
    assert refused.status_code == 409 and "default" in refused.json()["detail"]
    assert api.post(f"/api/v1/work/routes/{ok.json()['id']}/make-default").json()["is_default"] is True
    assert api.delete(f"/api/v1/work/routes/{standard['id']}").status_code == 204


def test_route_on_a_category_and_a_product(api):
    make, ship = _stage_id(api, "make"), _stage_id(api, "ship")
    route = api.post("/api/v1/work/routes", json={"name": "Coaster", "steps": [{"stage_id": make}, {"stage_id": ship}]}).json()
    category = api.post("/api/v1/product-categories", json={"name": "Coasters"}).json()
    assert category["work_route_id"] is None

    patched = api.patch(f"/api/v1/product-categories/{category['id']}", json={"name": "Coasters", "work_route_id": route["id"]})
    assert patched.status_code == 200 and patched.json()["work_route_id"] == route["id"]
    assert api.patch(f"/api/v1/product-categories/{category['id']}", json={"name": "Coasters", "work_route_id": 9999}).status_code == 404

    product = api.post("/api/v1/products", json={"name": "Wood coaster", "product_category_id": category["id"], "work_route_id": route["id"]})
    assert product.status_code == 201 and product.json()["work_route_id"] == route["id"]
    assert api.post("/api/v1/products", json={"name": "X", "work_route_id": 9999}).status_code == 404
    cleared = api.patch(f"/api/v1/products/{product.json()['id']}", json={"work_route_id": None})
    assert cleared.status_code == 200 and cleared.json()["work_route_id"] is None

    in_use = next(r for r in api.get("/api/v1/work/routes").json() if r["id"] == route["id"])
    assert in_use["category_count"] == 1
    refused = api.delete(f"/api/v1/work/routes/{route['id']}")
    assert refused.status_code == 409 and "1 category" in refused.json()["detail"]


def test_progress_validation_over_http(api):
    product = api.post("/api/v1/products", json={"name": "P"}).json()
    make = _stage_id(api, "make")
    refused = api.post("/api/v1/work/progress", json={"product_id": product["id"], "stage_id": make, "qty": 1})
    assert refused.status_code == 400 and "build" in refused.json()["detail"]
    assert api.post("/api/v1/work/progress/9999/undo").status_code == 404
