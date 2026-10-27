"""Hand-picked stock take scopes, and the "items due for counting" reminder."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.notification import (
    Notification,
    NotificationCategory,
    NotificationSettings,
    SummaryFrequency,
)
from app.models.product import Product
from app.schemas.stock_take import ScopeItemRef, StockTakeScope
from app.services import stock_take_reminder, stock_takes
from app.services.material_categories import legacy_value_for


async def _material(session, name, counted_at=None) -> Material:
    row = (await session.execute(select(MaterialCategory).where(MaterialCategory.name == "resin"))).scalar_one()
    m = Material(
        name=name,
        category=legacy_value_for("resin"),
        category_id=row.id,
        unit=MaterialUnit.ml,
        current_qty=Decimal(5),
        last_stock_take_at=counted_at,
    )
    session.add(m)
    await session.flush()
    return m


async def test_selected_items_narrow_the_take(session):
    session.add(GeneralSettings(id=1))
    a = await _material(session, "Grey Resin")
    await _material(session, "Black Resin")
    await session.commit()

    scope = StockTakeScope(include_materials=True, selected_items=[ScopeItemRef(material_id=a.id)])
    preview = await stock_takes.preview_scope(session, scope)
    assert preview.candidate_count == 1
    assert "1 hand-picked item" in preview.scope_description

    take, _ = await stock_takes.create_stock_take(session, scope)
    lines = (await session.execute(select(stock_takes.StockTakeLine))).scalars().all()
    assert [line.material_id for line in lines if line.stock_take_id == take.id] == [a.id]


async def test_scope_items_report_last_counted_and_due(session):
    session.add(GeneralSettings(id=1))
    counted = datetime(2026, 1, 5, tzinfo=timezone.utc)
    await _material(session, "Counted", counted_at=counted)
    await _material(session, "Never")
    await session.commit()

    items = await stock_takes.list_scope_items(session, StockTakeScope(include_materials=True))
    by_name = {i.name: i for i in items}
    assert by_name["Never"].last_stock_take_at is None
    assert by_name["Counted"].last_stock_take_at is not None
    assert by_name["Counted"].last_stock_take_at.date() == counted.date()


async def test_reminder_lists_due_items_per_category(session):
    session.add(GeneralSettings(id=1))
    await _material(session, "Grey Resin")
    await _material(session, "Black Resin")
    session.add(Product(name="Oak Coaster", sku="OAK", current_stock=3))
    await session.commit()

    counts = dict(await stock_take_reminder.due_counts_by_category(session))
    assert sum(counts.values()) == 3
    assert counts["Uncategorised products"] == 1


async def test_reminder_is_silent_when_nothing_is_due(session):
    session.add(GeneralSettings(id=1))
    settings = NotificationSettings(
        id=1,
        stock_take_reminder_enabled=True,
        stock_take_reminder_frequency=SummaryFrequency.daily,
        stock_take_reminder_hour_local=0,
    )
    session.add(settings)
    await session.commit()

    await stock_take_reminder.maybe_fire_stock_take_reminder(session, settings)

    sent = (
        await session.execute(select(Notification).where(Notification.category == NotificationCategory.stock_take_due))
    ).scalars().all()
    assert sent == []
    assert settings.stock_take_reminder_last_fired_at is not None


async def test_reminder_fires_when_items_are_due(session):
    session.add(GeneralSettings(id=1))
    await _material(session, "Grey Resin")
    settings = NotificationSettings(
        id=1,
        stock_take_reminder_enabled=True,
        stock_take_reminder_frequency=SummaryFrequency.daily,
        stock_take_reminder_hour_local=0,
    )
    session.add(settings)
    await session.commit()

    await stock_take_reminder.maybe_fire_stock_take_reminder(session, settings)

    sent = (
        await session.execute(select(Notification).where(Notification.category == NotificationCategory.stock_take_due))
    ).scalars().all()
    assert len(sent) == 1
    assert sent[0].title == "1 item due for counting"
