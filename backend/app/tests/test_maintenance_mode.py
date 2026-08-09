"""Maintenance mode: what a connected device sees while a restore is in flight, and the
host-only gate on restore itself.
"""

import asyncio

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.deps import get_db, require_host
from app.main import app
from app.models.base import Base
from app.models.general_settings import CurrencyCode, GeneralSettings
from app.services import maintenance


@pytest.fixture(autouse=True)
def clear_maintenance():
    maintenance.exit()
    yield
    maintenance.exit()


@pytest.fixture
def api_client(tmp_path):
    """The real app, pointed at a throwaway schema, with its lifespan deliberately not run.

    Two things here are deliberate and were both learned the hard way.

    Synchronous, not an async fixture: TestClient drives the app through its own event-loop
    portal, and an async fixture wanting a loop at the same time deadlocks.

    No `with TestClient(...)` block, so Starlette never runs the lifespan — which would start
    sync_scheduler and backup_scheduler against the real dev database and leave background tasks
    polling for the duration of the suite. Nothing under test here needs them.

    get_db is overridden because /system/status reads the database, and the dev database this
    process points at may be at any revision; these tests are about the middleware, not about
    whoever last ran a migration.
    """
    # File-backed rather than :memory: on purpose. Setup below runs in its own short-lived event
    # loop via asyncio.run, while the requests run on TestClient's portal loop. A StaticPool over
    # :memory: would have to carry one connection across both, and aiosqlite's worker thread ends
    # up calling into the first loop after it has closed — which surfaces as an unhandled thread
    # exception at the end of the run. A file lets each loop open its own connections.
    db_path = tmp_path / "maintenance-test.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    async def _setup() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as seed_session:
                seed_session.add(GeneralSettings(id=1, default_currency=CurrencyCode.GBP))
                await seed_session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_setup())

    request_engine = create_async_engine(url)
    factory = async_sessionmaker(request_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # A loopback client address, so require_host behaves as it would on the host machine.
    # TestClient's default is ("testclient", 50000), which require_host does not trust.
    yield TestClient(app, client=("127.0.0.1", 51234))
    app.dependency_overrides.pop(get_db, None)


class TestMaintenanceMiddleware:
    def test_api_routes_are_refused_while_restoring(self, api_client):
        maintenance.enter("restore_staged")
        response = api_client.get("/api/v1/materials")

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "10"
        body = response.json()
        assert body["maintenance"] is True
        assert body["phase"] == "restore_staged"

    def test_the_503_carries_cors_headers(self, api_client):
        """Load-bearing, and the reason the middleware is registered inside CORSMiddleware.

        Starlette wraps middleware in reverse registration order. Get this backwards and a thin
        client's fetch fails opaquely — it can't read the response at all, so a maintenance
        window is indistinguishable from a dead backend and the overlay never shows the right
        message.
        """
        maintenance.enter("restoring")
        response = api_client.get("/api/v1/materials", headers={"Origin": "http://localhost:1420"})

        assert response.status_code == 503
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_status_stays_reachable_while_restoring(self, api_client):
        """The endpoint a locked-out client polls to learn when the outage ends must not be
        behind the wall it is polling about."""
        maintenance.enter("restore_staged")

        assert api_client.get("/healthz").status_code == 200

        response = api_client.get("/system/status")
        assert response.status_code == 200
        assert response.json()["status"] == "maintenance"
        assert response.json()["phase"] == "restore_staged"

    def test_cancelling_a_staged_restore_stays_reachable(self, api_client):
        """The escape hatch from the state the middleware enforces has to work while that state
        is active, or a staged restore that is never applied locks the app permanently."""
        maintenance.enter("restore_staged")
        response = api_client.delete("/api/v1/restore/pending")

        assert response.status_code != 503

    def test_everything_works_normally_when_not_restoring(self, api_client):
        response = api_client.get("/system/status")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["phase"] is None


class TestRequireHost:
    """`require_host` is what stops a thin client killing the backend for everyone.

    Mounted on a throwaway app so the test is about the dependency itself, not whatever auth or
    state the restore router also wants.
    """

    @pytest.fixture
    def host_app(self):
        probe = FastAPI()

        @probe.get("/probe", dependencies=[Depends(require_host)])
        async def _probe() -> dict:
            return {"ok": True}

        return probe

    def test_allows_loopback(self, host_app):
        with TestClient(host_app, client=("127.0.0.1", 51234)) as client:
            assert client.get("/probe").status_code == 200

    def test_refuses_another_device(self, host_app):
        # A Tailscale address — the realistic case, a second laptop pointed at the host.
        with TestClient(host_app, client=("100.64.0.7", 51234)) as client:
            response = client.get("/probe")

        assert response.status_code == 403
        # The message has to say *why*: from the other device this otherwise looks arbitrary.
        assert "computer that hosts StockSmith" in response.json()["detail"]

    def test_a_forwarded_header_cannot_fake_loopback(self, host_app):
        """require_host reads request.client, which no header can influence. Pinned because the
        moment a reverse proxy is introduced this stops being true, and the docstring says so.
        """
        with TestClient(host_app, client=("100.64.0.7", 51234)) as client:
            response = client.get("/probe", headers={"X-Forwarded-For": "127.0.0.1"})

        assert response.status_code == 403
