"""Shared test fixtures.

Deliberately builds the schema with `Base.metadata.create_all` rather than running
alembic: the models are the source of truth for what the app expects, the baseline
migration is large, and a test suite that reruns migrations tests alembic rather than the
code under test.

Everything here is aimed at making `order_sync.commit_sync` runnable with no HTTP, no
marketplace account, and no touching the real database — that pipeline is where the
paid-only import gate actually lives, so being able to drive it end-to-end in-process is
the whole point.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.db import enforce_sqlite_foreign_keys
from app.models.base import Base
from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.services import listing_push, order_sync
from app.services.platforms.base import ExternalOrder, ExternalOrderLine, PaymentState


@pytest_asyncio.fixture
async def engine():
    # StaticPool + a single shared in-memory connection: commit_sync deliberately opens
    # two sequential sessions (fetch phase, then write phase), and without this each would
    # get its own blank database.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Match production: app/db.py turns foreign-key enforcement on for every SQLite
    # connection, and SQLite's default is off. Without this the suite would silently
    # tolerate dangling references and unfired cascades that the real app rejects — the
    # exact gap that let order deletion orphan its audit rows for weeks.
    # test_foreign_key_enforcement.py deliberately builds its own engines instead of using
    # this fixture, since it needs to compare enforced against unenforced behaviour.
    enforce_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # commit_sync builds its own sessions from this module-level factory rather than
    # taking one from the caller, so redirecting it is the only way to keep the test off
    # the real dev database.
    monkeypatch.setattr(order_sync, "async_session_factory", factory)
    return factory


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def pushes(monkeypatch):
    """Records outbound listing-quantity pushes instead of performing them.

    Not merely isolation: listing_push is what makes a wrongly-imported order *externally*
    visible, by lowering the quantity on the real marketplace listing seconds later. Tests
    assert this stays empty for a skipped order, which is the part of the bug a user would
    actually have noticed.
    """
    recorded: list[tuple] = []
    monkeypatch.setattr(listing_push, "enqueue_for_owner", lambda owner: recorded.append(("owner", owner)))
    monkeypatch.setattr(
        listing_push, "enqueue_for_material", lambda session, material_id: recorded.append(("material", material_id))
    )
    return recorded


@pytest_asyncio.fixture
async def connection(session):
    """A connected Etsy connection with no sync history and no start-date floor, so tests
    control the fetch window entirely through what the fake adapter returns."""
    conn = PlatformConnection(
        platform=ListingPlatform.etsy,
        access_token="test-access",
        refresh_token="test-refresh",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        external_account_id="12345",
        sync_start_date=None,
        auto_sync_enabled=True,
    )
    session.add(conn)
    await session.commit()
    return conn


class FakeAdapter:
    """Returns a scripted batch of ExternalOrders. Satisfies the parts of the
    PlatformAdapter Protocol that commit_sync/preview_sync actually reach."""

    def __init__(self, orders: list[ExternalOrder]):
        self.orders = orders
        self.since_values: list[datetime | None] = []

    async def fetch_orders_since(self, session, connection, since):
        self.since_values.append(since)
        return list(self.orders)


@pytest.fixture
def use_adapter(monkeypatch):
    """Swaps in a FakeAdapter. order_sync imports get_adapter by name, so patch it there
    rather than on the registry module."""

    def _use(orders: list[ExternalOrder]) -> FakeAdapter:
        adapter = FakeAdapter(orders)

        async def _get_adapter(session, platform, environment=None):
            return adapter

        monkeypatch.setattr(order_sync, "get_adapter", _get_adapter)
        return adapter

    return _use


def make_order(
    external_order_id: str = "R1",
    *,
    payment_state: PaymentState = PaymentState.settled,
    sku: str | None = None,
    qty: int = 1,
    placed_at: datetime | None = None,
    last_modified: datetime | None = None,
    is_cancelled: bool = False,
    is_shipped: bool = False,
    financials_enriched: bool = True,
    raw: dict | None = None,
    **financials,
) -> ExternalOrder:
    now = datetime.now(timezone.utc)
    return ExternalOrder(
        external_order_id=external_order_id,
        buyer_name=None,
        buyer_note=None,
        placed_at=placed_at or now,
        last_modified=last_modified or placed_at or now,
        is_cancelled=is_cancelled,
        is_shipped=is_shipped,
        lines=[
            ExternalOrderLine(
                external_line_id=f"{external_order_id}-L1", sku=sku, qty=qty, unit_price="10.00", currency="GBP"
            )
        ],
        raw=raw if raw is not None else {},
        payment_state=payment_state,
        financials_enriched=financials_enriched,
        **financials,
    )
