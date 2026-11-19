"""The sample workspace's generator runs end to end against a fresh database, through the app's
own functions, and leaves the app in the state the Dashboard is meant to show off."""

from sqlalchemy import func, select

from app.models.general_settings import GeneralSettings
from app.models.material import Material
from app.models.order import Order
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.variant import ProductVariant
from app.seed import ensure_seed_data
from app.services.buildability import compute_dashboard_summary
from app.services.sample_data import build_sample


async def _count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def test_builds_a_believable_candle_shop(session, pushes):
    await ensure_seed_data(session)
    await build_sample(session)

    settings = await session.get(GeneralSettings, 1)
    assert settings.business_type == "candles_soap"
    assert settings.setup_checklist_dismissed is True

    assert await _count(session, Material) == 13
    assert await _count(session, Product) == 3
    assert await _count(session, ProductVariant) == 4
    assert await _count(session, Order) == 23
    assert await _count(session, Order, Order.shipped_at.is_not(None)) >= 15
    assert await _count(session, Purchase, Purchase.received_at.is_(None)) == 1

    # Stock and cost came from a received purchase, so wax has both.
    wax = (await session.scalars(select(Material).where(Material.name == "Soy container wax"))).one()
    assert wax.current_qty > 0 and wax.avg_unit_cost > 0

    # The newest order wants more large sea-salt candles than exist: something to build.
    summary = await compute_dashboard_summary(session)
    assert summary.orders_awaiting_inventory
