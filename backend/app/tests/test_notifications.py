"""Notifications backend: alert dedup, quiet hours, daily/weekly summary scheduling, and
the summary's cost/profit aggregation (reusing routers/orders.py's own COGS logic).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.notification import (
    Notification,
    NotificationCategory,
    NotificationDeliveryMode,
    NotificationSettings,
    NotificationTypeSettings,
    NotificationUrgency,
    SummaryFrequency,
)
from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.schemas.dashboard import OrderAwaitingInventory
from app.services import notification_alerts, notification_summary, notifications
from app.services.forecasting import MaterialForecast


def _forecast(material_id: int, status: str, name: str = "Filament") -> MaterialForecast:
    return MaterialForecast(
        material_id=material_id,
        name=name,
        current_qty=Decimal(10),
        allocated_qty=Decimal(0),
        on_order_qty=Decimal(0),
        reorder_threshold=Decimal(5),
        supplier_id=None,
        supplier_name=None,
        consumption_rate_per_week=Decimal(1),
        weeks_of_supply=Decimal(1) if status != "ok" else Decimal(10),
        fg_buffer_weeks=None,
        lead_time_days=5,
        status=status,
    )


async def _set_type(session, category: NotificationCategory, *, enabled=True, delivery_mode=NotificationDeliveryMode.immediate):
    result = await session.execute(select(NotificationTypeSettings).where(NotificationTypeSettings.alert_type == category))
    row = result.scalar_one_or_none()
    if row is None:
        row = NotificationTypeSettings(alert_type=category, enabled=enabled, delivery_mode=delivery_mode)
        session.add(row)
    else:
        row.enabled = enabled
        row.delivery_mode = delivery_mode
    await session.commit()


async def _notification_titles(session) -> list[str]:
    result = await session.execute(select(Notification).order_by(Notification.id))
    return [n.title for n in result.scalars()]


class TestMaterialForecastDedup:
    async def test_fires_once_on_transition_into_critical(self, session, monkeypatch):
        await _set_type(session, NotificationCategory.material_forecast_critical)
        await _set_type(session, NotificationCategory.material_forecast_warning)

        monkeypatch.setattr(notification_alerts, "compute_material_forecasts", lambda s: _forecasts_stub(["critical"]))
        await notification_alerts.check_material_forecast_alerts(session)
        assert len(await _notification_titles(session)) == 1

        # Still critical on the next poll — must not refire.
        await notification_alerts.check_material_forecast_alerts(session)
        assert len(await _notification_titles(session)) == 1

    async def test_refires_after_recovering_then_going_critical_again(self, session, monkeypatch):
        await _set_type(session, NotificationCategory.material_forecast_critical)
        await _set_type(session, NotificationCategory.material_forecast_warning)

        monkeypatch.setattr(notification_alerts, "compute_material_forecasts", lambda s: _forecasts_stub(["critical"]))
        await notification_alerts.check_material_forecast_alerts(session)

        monkeypatch.setattr(notification_alerts, "compute_material_forecasts", lambda s: _forecasts_stub(["ok"]))
        await notification_alerts.check_material_forecast_alerts(session)

        monkeypatch.setattr(notification_alerts, "compute_material_forecasts", lambda s: _forecasts_stub(["critical"]))
        await notification_alerts.check_material_forecast_alerts(session)

        assert len(await _notification_titles(session)) == 2

    async def test_disabled_type_never_fires(self, session, monkeypatch):
        await _set_type(session, NotificationCategory.material_forecast_critical, enabled=False)
        await _set_type(session, NotificationCategory.material_forecast_warning, enabled=False)

        monkeypatch.setattr(notification_alerts, "compute_material_forecasts", lambda s: _forecasts_stub(["critical"]))
        await notification_alerts.check_material_forecast_alerts(session)
        assert await _notification_titles(session) == []


async def _forecasts_stub(statuses: list[str]):
    return [_forecast(i + 1, status) for i, status in enumerate(statuses)]


class TestOrderUnfulfillableDedup:
    def _awaiting(self, line_id: int) -> OrderAwaitingInventory:
        return OrderAwaitingInventory(
            line_id=line_id,
            order_id=100 + line_id,
            product_id=1,
            variant_id=None,
            product_name="Widget",
            variant_name=None,
            short_by=3,
            order_placed_at=datetime.now(timezone.utc),
            platform=None,
        )

    async def test_fires_once_then_stays_quiet_while_still_short(self, session, monkeypatch):
        await _set_type(session, NotificationCategory.order_unfulfillable)
        monkeypatch.setattr(
            notification_alerts, "get_orders_awaiting_inventory", lambda s: _awaiting_stub([self._awaiting(1)])
        )

        await notification_alerts.check_order_unfulfillable_alerts(session)
        await notification_alerts.check_order_unfulfillable_alerts(session)

        assert len(await _notification_titles(session)) == 1

    async def test_refires_after_resolving_and_going_short_again(self, session, monkeypatch):
        await _set_type(session, NotificationCategory.order_unfulfillable)
        line = self._awaiting(2)

        monkeypatch.setattr(notification_alerts, "get_orders_awaiting_inventory", lambda s: _awaiting_stub([line]))
        await notification_alerts.check_order_unfulfillable_alerts(session)

        monkeypatch.setattr(notification_alerts, "get_orders_awaiting_inventory", lambda s: _awaiting_stub([]))
        await notification_alerts.check_order_unfulfillable_alerts(session)

        monkeypatch.setattr(notification_alerts, "get_orders_awaiting_inventory", lambda s: _awaiting_stub([line]))
        await notification_alerts.check_order_unfulfillable_alerts(session)

        assert len(await _notification_titles(session)) == 2


async def _awaiting_stub(items):
    return list(items)


class TestQuietHours:
    def _settings(self, **overrides) -> NotificationSettings:
        base = dict(quiet_hours_enabled=True, quiet_hours_start=22, quiet_hours_end=7)
        base.update(overrides)
        return NotificationSettings(id=1, **base)

    def test_inside_an_overnight_window(self):
        settings = self._settings()
        assert notifications._in_quiet_hours(settings, now=datetime(2026, 1, 1, 23, 0)) is True
        assert notifications._in_quiet_hours(settings, now=datetime(2026, 1, 1, 3, 0)) is True

    def test_outside_an_overnight_window(self):
        settings = self._settings()
        assert notifications._in_quiet_hours(settings, now=datetime(2026, 1, 1, 12, 0)) is False

    def test_disabled_is_never_quiet(self):
        settings = self._settings(quiet_hours_enabled=False)
        assert notifications._in_quiet_hours(settings, now=datetime(2026, 1, 1, 23, 0)) is False

    def test_zero_width_window_is_treated_as_disabled(self):
        settings = self._settings(quiet_hours_start=5, quiet_hours_end=5)
        assert notifications._in_quiet_hours(settings, now=datetime(2026, 1, 1, 5, 0)) is False

    async def test_immediate_urgency_bypasses_quiet_hours(self, session):
        settings = await notifications.get_notification_settings(session)
        settings.quiet_hours_enabled = True
        settings.quiet_hours_start = 0
        settings.quiet_hours_end = 23
        settings.pushover_enabled = False
        await session.commit()

        notification = await notifications.dispatch_notification(
            session,
            category=NotificationCategory.daily_summary,
            urgency=NotificationUrgency.immediate,
            title="Daily order summary",
            body="body",
            delivery_mode=NotificationDeliveryMode.immediate,
        )
        assert notification is not None
        assert notification.read_at is None  # in-app record always stands regardless of quiet hours

    async def test_off_delivery_mode_skips_the_in_app_log_entirely(self, session):
        notification = await notifications.dispatch_notification(
            session,
            category=NotificationCategory.pending_order_threshold,
            urgency=NotificationUrgency.digest,
            title="Should never be logged",
            body="body",
            delivery_mode=NotificationDeliveryMode.off,
        )
        assert notification is None
        assert await _notification_titles(session) == []


class TestSummaryDueCalculation:
    def _settings(self, **overrides) -> NotificationSettings:
        base = dict(
            daily_summary_enabled=True,
            daily_summary_frequency=SummaryFrequency.daily,
            daily_summary_hour_local=18,
            daily_summary_day_of_week=None,
            daily_summary_last_fired_at=None,
        )
        base.update(overrides)
        return NotificationSettings(id=1, **base)

    @pytest.mark.parametrize(
        "hour,last_run_iso,expected",
        [
            (18, None, True),
            (18, "2026-08-09T18:30:00", False),
            (18, "2026-08-08T18:30:00", True),
            (18, "2026-08-09T10:00:00", True),
            (10, None, False),
        ],
    )
    def test_daily_due_calculation(self, hour, last_run_iso, expected):
        last = datetime.fromisoformat(last_run_iso).astimezone() if last_run_iso else None
        settings = self._settings(daily_summary_last_fired_at=last)
        now = datetime(2026, 8, 9, hour, 0, 0)
        assert notification_summary._is_due(now, settings) is expected

    def test_disabled_never_due(self):
        settings = self._settings(daily_summary_enabled=False)
        assert notification_summary._is_due(datetime(2026, 8, 9, 18, 0, 0), settings) is False

    def test_weekly_only_fires_on_the_configured_day(self):
        settings = self._settings(daily_summary_frequency=SummaryFrequency.weekly, daily_summary_day_of_week=0)  # Monday
        monday = datetime(2026, 8, 10, 18, 0, 0)  # a Monday
        tuesday = datetime(2026, 8, 11, 18, 0, 0)
        assert monday.weekday() == 0
        assert notification_summary._is_due(monday, settings) is True
        assert notification_summary._is_due(tuesday, settings) is False

    def test_weekly_does_not_refire_later_the_same_day(self):
        settings = self._settings(
            daily_summary_frequency=SummaryFrequency.weekly,
            daily_summary_day_of_week=0,
            daily_summary_last_fired_at=datetime(2026, 8, 10, 18, 5, 0, tzinfo=timezone.utc),
        )
        later_same_monday = datetime(2026, 8, 10, 19, 0, 0)
        assert notification_summary._is_due(later_same_monday, settings) is False

    def test_weekly_with_no_day_configured_never_fires(self):
        settings = self._settings(daily_summary_frequency=SummaryFrequency.weekly, daily_summary_day_of_week=None)
        assert notification_summary._is_due(datetime(2026, 8, 10, 18, 0, 0), settings) is False


class TestSummaryAggregation:
    def _order(
        self,
        *,
        product_id: int,
        shipped_at: datetime,
        subtotal,
        shipping_charged,
        payment_fees,
        shipping_cost_snapshot,
        shipped_qty: int,
        cost_per_unit_snapshot,
    ) -> Order:
        order = Order(
            status=OrderStatus.shipped,
            shipped_at=shipped_at,
            subtotal=subtotal,
            shipping_charged=shipping_charged,
            payment_fees=payment_fees,
            shipping_cost_snapshot=shipping_cost_snapshot,
        )
        order.lines = [
            OrderLine(
                ordered_qty=shipped_qty,
                allocated_qty=shipped_qty,
                shipped_qty=shipped_qty,
                product_id=product_id,
                needs_mapping=False,
                cost_per_unit_snapshot=cost_per_unit_snapshot,
            )
        ]
        return order

    async def test_aggregates_revenue_profit_and_items_and_flags_pending_cogs(self, session, monkeypatch):
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        window_start = now - timedelta(days=1)

        product = Product(name="Widget")
        session.add(product)
        await session.flush()

        clean_order = self._order(
            product_id=product.id,
            shipped_at=now - timedelta(hours=1),
            subtotal=Decimal("50.00"),
            shipping_charged=Decimal("5.00"),
            payment_fees=Decimal("3.00"),
            shipping_cost_snapshot=Decimal("2.00"),
            shipped_qty=2,
            cost_per_unit_snapshot=Decimal("10.00"),
        )
        pending_order = self._order(
            product_id=product.id,
            shipped_at=now - timedelta(hours=2),
            subtotal=Decimal("30.00"),
            shipping_charged=Decimal("4.00"),
            payment_fees=Decimal("2.00"),
            shipping_cost_snapshot=Decimal("1.50"),
            shipped_qty=1,
            cost_per_unit_snapshot=None,  # never allocated a cost — COGS pending
        )
        too_old_order = self._order(
            product_id=product.id,
            shipped_at=window_start - timedelta(days=1),
            subtotal=Decimal("999.00"),
            shipping_charged=Decimal("0"),
            payment_fees=Decimal("0"),
            shipping_cost_snapshot=Decimal("0"),
            shipped_qty=99,
            cost_per_unit_snapshot=Decimal("0"),
        )
        session.add_all([clean_order, pending_order, too_old_order])
        await session.commit()

        settings = await notifications.get_notification_settings(session)
        settings.daily_summary_enabled = True
        settings.daily_summary_hour_local = 12
        settings.daily_summary_last_fired_at = window_start
        settings.pushover_enabled = False
        await session.commit()

        monkeypatch.setattr(notification_summary, "get_kitting_cogs_by_order", _no_kitting_cogs)
        monkeypatch.setattr(notification_summary, "datetime", _FixedDatetime(now=now, utcnow=now))

        await notification_summary.maybe_fire_order_summary(session, settings)

        result = await session.execute(select(Notification).where(Notification.category == NotificationCategory.daily_summary))
        summary = result.scalar_one()

        assert "2 orders shipped, 3 items" in summary.body
        # revenue = (50+5) + (30+4) = 89.00
        assert "Revenue: 89.00" in summary.body
        # clean order profit = 50+5-3-2-(10*2) = 30.00; pending order has no cost snapshot, so
        # _compute_net_profit treats its materials cost as £0: 30+4-2-1.5-0 = 30.50. Total: 60.50.
        assert "Net profit: 60.50" in summary.body
        assert "1 order" in summary.body and "pending cost sync" in summary.body

        refreshed_settings = await notifications.get_notification_settings(session)
        assert refreshed_settings.daily_summary_last_fired_at is not None

    async def test_no_shipped_orders_advances_watermark_without_dispatching(self, session, monkeypatch):
        settings = await notifications.get_notification_settings(session)
        settings.daily_summary_enabled = True
        settings.daily_summary_hour_local = 12
        settings.daily_summary_last_fired_at = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
        await session.commit()

        fixed_now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(notification_summary, "datetime", _FixedDatetime(now=fixed_now, utcnow=fixed_now))

        await notification_summary.maybe_fire_order_summary(session, settings)

        result = await session.execute(select(Notification).where(Notification.category == NotificationCategory.daily_summary))
        assert result.scalar_one_or_none() is None


async def _no_kitting_cogs(session, order_ids):
    return {}


class _FixedDatetime:
    """A drop-in for the `datetime` name inside notification_summary: `.now()` (naive,
    for `_is_due`'s local-hour check) and `.now(timezone.utc)` (aware, for the query
    window) both resolve to fixed values instead of the real wall clock."""

    def __init__(self, *, now: datetime, utcnow: datetime):
        self._now = now
        self._utcnow = utcnow

    def now(self, tz=None):
        return self._now if tz is None else self._utcnow.astimezone(tz)
