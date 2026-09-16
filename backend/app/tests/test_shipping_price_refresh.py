"""The scheduled refresh of linked shipping profiles' buyer prices (Stage 3 of
docs/plan-shipping-profile-marketplace-link.md).

The refresh overwrites without asking, so what's pinned here is everything that makes that
safe: it writes only what changed and only price_<platform>, every change is recorded with
its source (the refresh's, the manual import's and a Settings edit's alike), one alert
lists what moved, a profile that vanished upstream keeps its price and raises the
immediate alert once, calculated profiles are skipped silently, a rate limit leaves the
timestamp unset so the next tick retries, and the tick honours the refresh window.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.listing import ListingPlatform
from app.models.notification import Notification, NotificationCategory, NotificationDeliveryMode, NotificationTypeSettings
from app.models.platform_connection import PlatformConnection
from app.models.shipping_profile import PriceEventSource, ShippingProfile, ShippingProfilePriceEvent
from app.routers import shipping_profiles as router
from app.schemas.shipping_profile import ShippingProfileUpdate
from app.services import shipping_price_sync, sync_scheduler
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


async def _connect(session, platform=ETSY, **overrides) -> PlatformConnection:
    fields = dict(
        platform=platform,
        access_token="token",
        refresh_token="refresh",
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        external_account_id="shop-1",
        auto_sync_enabled=True,
    )
    fields.update(overrides)
    conn = PlatformConnection(**fields)
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def _profile(session, name="Small parcel", **kwargs) -> ShippingProfile:
    profile = ShippingProfile(name=name, price=Decimal("3.00"), **kwargs)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def _enable(session, category, delivery_mode=NotificationDeliveryMode.immediate):
    session.add(NotificationTypeSettings(alert_type=category, enabled=True, delivery_mode=delivery_mode))
    await session.commit()


def _mp(id="501", title="Small parcel", price="3.60", calculated=False):
    return shipping_price_sync.MarketplaceProfile(
        id=id,
        title=title,
        is_calculated=calculated,
        domestic_price=Decimal(price) if price is not None else None,
        domestic_fallback=False,
    )


@pytest.fixture
def marketplace(monkeypatch):
    calls: list[ListingPlatform] = []

    def _use(profiles=None, *, error: Exception | None = None):
        async def _fetch(session, platform):
            calls.append(platform)
            if error is not None:
                raise error
            return list(profiles or [])

        monkeypatch.setattr(shipping_price_sync, "fetch_marketplace_profiles", _fetch)
        return calls

    return _use


async def _events(session, profile_id: int) -> list[ShippingProfilePriceEvent]:
    result = await session.execute(
        select(ShippingProfilePriceEvent)
        .where(ShippingProfilePriceEvent.shipping_profile_id == profile_id)
        .order_by(ShippingProfilePriceEvent.id)
    )
    return list(result.scalars())


async def _notifications(session) -> list[Notification]:
    return list((await session.execute(select(Notification).order_by(Notification.id))).scalars())


class TestRefresh:
    async def test_writes_only_changed_channel_prices_and_records_each(self, session, marketplace):
        await _connect(session)
        moved = await _profile(session, "Moved", etsy_shipping_profile_id=501, price_etsy=Decimal("3.00"))
        same = await _profile(session, "Same", etsy_shipping_profile_id=502, price_etsy=Decimal("2.50"))
        unlinked = await _profile(session, "Unlinked", price_etsy=Decimal("9.99"))
        marketplace([_mp(id="501", price="3.60"), _mp(id="502", title="Same", price="2.50")])

        result = await shipping_price_sync.refresh(session, ETSY)

        assert [c.profile.id for c in result.changed] == [moved.id]
        assert [p.id for p in result.unchanged] == [same.id]
        assert result.refreshed_at is not None
        assert Decimal(moved.price_etsy) == Decimal("3.60")
        # The default price and the seller costs are not the marketplace's to change.
        assert Decimal(moved.price) == Decimal("3.00")
        assert Decimal(unlinked.price_etsy) == Decimal("9.99")

        events = await _events(session, moved.id)
        assert len(events) == 1
        assert events[0].source == PriceEventSource.sync
        assert (Decimal(events[0].old_price), Decimal(events[0].new_price)) == (Decimal("3.00"), Decimal("3.60"))
        assert await _events(session, same.id) == []

    async def test_first_import_from_unset_is_recorded_from_none(self, session, marketplace):
        await _connect(session)
        profile = await _profile(session, etsy_shipping_profile_id=501)
        marketplace([_mp()])
        await shipping_price_sync.refresh(session, ETSY)
        (event,) = await _events(session, profile.id)
        assert event.old_price is None and Decimal(event.new_price) == Decimal("3.60")

    async def test_one_alert_per_refresh_naming_every_profile_that_moved(self, session, marketplace):
        await _connect(session)
        await _enable(session, NotificationCategory.shipping_price_changed, NotificationDeliveryMode.digest)
        await _profile(session, "Letter", etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"))
        await _profile(session, "Parcel", etsy_shipping_profile_id=502, price_etsy=Decimal("3.00"))
        await _profile(session, "Same", etsy_shipping_profile_id=503, price_etsy=Decimal("2.00"))
        marketplace([_mp(id="501", price="1.20"), _mp(id="502", price="3.40"), _mp(id="503", price="2.00")])

        await shipping_price_sync.refresh(session, ETSY)

        notes = await _notifications(session)
        assert len(notes) == 1
        assert notes[0].category == NotificationCategory.shipping_price_changed
        assert "2 shipping profiles" in notes[0].title
        assert "Letter: £1.00 → £1.20" in notes[0].body
        assert "Parcel: £3.00 → £3.40" in notes[0].body
        assert "Same" not in notes[0].body

    async def test_no_alert_when_nothing_moved(self, session, marketplace):
        await _connect(session)
        await _enable(session, NotificationCategory.shipping_price_changed)
        await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("3.60"))
        marketplace([_mp()])
        await shipping_price_sync.refresh(session, ETSY)
        assert await _notifications(session) == []

    async def test_deleted_upstream_keeps_the_price_and_alerts_once(self, session, marketplace):
        """Drafts pushed with that id will fail and margin is running on an unverifiable
        number — worth an immediate alert, but the same missing profile the next day is
        not news."""
        await _connect(session)
        await _enable(session, NotificationCategory.shipping_profile_missing)
        gone = await _profile(session, "Gone", etsy_shipping_profile_id=999, price_etsy=Decimal("4.00"))
        marketplace([_mp()])

        result = await shipping_price_sync.refresh(session, ETSY)
        assert [p.id for p in result.missing_upstream] == [gone.id]
        assert Decimal(gone.price_etsy) == Decimal("4.00")
        assert gone.etsy_shipping_profile_id == 999  # never cleared automatically

        await shipping_price_sync.refresh(session, ETSY)
        notes = await _notifications(session)
        assert len(notes) == 1
        assert notes[0].category == NotificationCategory.shipping_profile_missing
        assert "Gone" in notes[0].title

    async def test_a_profile_that_reappears_can_alert_again_later(self, session, marketplace):
        await _connect(session)
        await _enable(session, NotificationCategory.shipping_profile_missing)
        await _profile(session, "Flaky", etsy_shipping_profile_id=501, price_etsy=Decimal("3.60"))

        marketplace([])
        await shipping_price_sync.refresh(session, ETSY)
        marketplace([_mp()])
        await shipping_price_sync.refresh(session, ETSY)
        marketplace([])
        await shipping_price_sync.refresh(session, ETSY)

        assert len(await _notifications(session)) == 2

    async def test_calculated_profiles_are_skipped_silently(self, session, marketplace):
        await _connect(session)
        await _enable(session, NotificationCategory.shipping_price_changed)
        await _enable(session, NotificationCategory.shipping_profile_missing)
        calc = await _profile(session, "Calc", etsy_shipping_profile_id=501, price_etsy=Decimal("5.00"))
        marketplace([_mp(price=None, calculated=True)])

        result = await shipping_price_sync.refresh(session, ETSY)

        assert [p.id for p in result.skipped_calculated] == [calc.id]
        assert Decimal(calc.price_etsy) == Decimal("5.00")
        assert await _notifications(session) == []
        assert await _events(session, calc.id) == []

    async def test_archived_profiles_are_left_alone(self, session, marketplace):
        await _connect(session)
        retired = await _profile(session, "Retired", etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"), is_archived=True)
        marketplace([_mp(price="3.60")])
        result = await shipping_price_sync.refresh(session, ETSY)
        assert result.changed == [] and result.missing_upstream == []
        assert Decimal(retired.price_etsy) == Decimal("1.00")

    async def test_rate_limit_writes_nothing_and_leaves_the_timestamp_unset(self, session, marketplace):
        conn = await _connect(session)
        profile = await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"))
        marketplace(error=PlatformRateLimitError("slow down"))

        result = await shipping_price_sync.refresh(session, ETSY)

        assert result.error == "slow down" and result.refreshed_at is None
        assert Decimal(profile.price_etsy) == Decimal("1.00")
        await session.refresh(conn)
        assert conn.last_shipping_price_refresh_at is None

    async def test_a_read_failure_advances_the_timestamp_so_it_is_not_retried_every_tick(self, session, marketplace):
        conn = await _connect(session)
        await _profile(session, etsy_shipping_profile_id=501)
        marketplace(error=PlatformSyncError("403 shops_r"))
        result = await shipping_price_sync.refresh(session, ETSY)
        assert "403" in result.error
        await session.refresh(conn)
        assert conn.last_shipping_price_refresh_at is not None

    async def test_auth_errors_propagate_to_the_sync_loop(self, session, marketplace):
        await _connect(session)
        await _profile(session, etsy_shipping_profile_id=501)
        marketplace(error=PlatformAuthError("revoked"))
        with pytest.raises(PlatformAuthError):
            await shipping_price_sync.refresh(session, ETSY)

    async def test_no_linked_profiles_means_no_marketplace_call(self, session, marketplace):
        conn = await _connect(session)
        await _profile(session)
        calls = marketplace([_mp()])
        await shipping_price_sync.refresh(session, ETSY)
        assert calls == []
        await session.refresh(conn)
        assert conn.last_shipping_price_refresh_at is not None

    async def test_ebay_matches_on_the_policy_id(self, session, marketplace):
        await _connect(session, EBAY)
        profile = await _profile(session, ebay_fulfillment_policy_id="6001", price_etsy=Decimal("9.00"))
        marketplace([_mp(id="6001", title="Royal Mail", price="3.20")])
        await shipping_price_sync.refresh(session, EBAY)
        assert Decimal(profile.price_ebay) == Decimal("3.20")
        assert Decimal(profile.price_etsy) == Decimal("9.00")


class TestRefreshWindow:
    def test_due_when_never_refreshed(self):
        conn = PlatformConnection(platform=ETSY, shipping_price_refresh_hours=24)
        assert shipping_price_sync.is_refresh_due(conn) is True

    def test_not_due_inside_the_window(self):
        now = datetime.now(timezone.utc)
        conn = PlatformConnection(
            platform=ETSY, shipping_price_refresh_hours=24, last_shipping_price_refresh_at=now - timedelta(hours=23)
        )
        assert shipping_price_sync.is_refresh_due(conn, now) is False

    def test_due_once_the_window_has_elapsed(self):
        now = datetime.now(timezone.utc)
        conn = PlatformConnection(
            platform=ETSY, shipping_price_refresh_hours=6, last_shipping_price_refresh_at=now - timedelta(hours=6)
        )
        assert shipping_price_sync.is_refresh_due(conn, now) is True

    async def test_the_tick_runs_the_refresh_only_when_due(self, session, session_factory, marketplace, monkeypatch):
        """The refresh rides on a successful order sync; inside the window it is skipped
        without touching the marketplace."""
        conn = await _connect(session, last_shipping_price_refresh_at=datetime.now(timezone.utc))
        await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"))
        calls = marketplace([_mp(price="3.60")])
        monkeypatch.setattr(shipping_price_sync, "async_session_factory", session_factory)
        monkeypatch.setattr(sync_scheduler, "async_session_factory", session_factory)

        async def _synced(_platform):
            return None

        monkeypatch.setattr(sync_scheduler, "run_commit_sync_guarded", _synced)

        await sync_scheduler._tick(ETSY)
        assert calls == []

        conn.last_shipping_price_refresh_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await session.commit()
        await sync_scheduler._tick(ETSY)
        assert calls == [ETSY]

    async def test_a_failed_order_sync_skips_the_refresh(self, session, session_factory, marketplace, monkeypatch):
        await _connect(session)
        await _profile(session, etsy_shipping_profile_id=501)
        calls = marketplace([_mp()])
        monkeypatch.setattr(shipping_price_sync, "async_session_factory", session_factory)
        monkeypatch.setattr(sync_scheduler, "async_session_factory", session_factory)

        async def _failed(_platform):
            raise PlatformRateLimitError("busy")

        monkeypatch.setattr(sync_scheduler, "run_commit_sync_guarded", _failed)
        await sync_scheduler._tick(ETSY)
        assert calls == []


class TestManualPaths:
    async def test_the_import_endpoint_records_a_manual_import_event(self, session, marketplace):
        profile = await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("3.00"))
        marketplace([_mp()])
        await router.import_shipping_profile_price(profile.id, ETSY, session=session)
        (event,) = await _events(session, profile.id)
        assert event.source == PriceEventSource.manual_import
        assert event.platform == ETSY

    async def test_a_settings_edit_records_a_user_edit_event_and_an_unchanged_one_does_not(self, session):
        profile = await _profile(session, price_etsy=Decimal("3.00"))
        await router.update_shipping_profile(profile.id, ShippingProfileUpdate(price_etsy=Decimal("3.25")), session=session)
        await router.update_shipping_profile(profile.id, ShippingProfileUpdate(price_etsy=Decimal("3.25")), session=session)
        await router.update_shipping_profile(profile.id, ShippingProfileUpdate(name="Renamed"), session=session)
        (event,) = await _events(session, profile.id)
        assert event.source == PriceEventSource.user_edit
        assert (Decimal(event.old_price), Decimal(event.new_price)) == (Decimal("3.00"), Decimal("3.25"))

    async def test_the_manual_endpoint_runs_without_waiting_for_the_window(self, session, marketplace):
        conn = await _connect(session, last_shipping_price_refresh_at=datetime.now(timezone.utc))
        profile = await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"))
        marketplace([_mp(price="3.60"), _mp(id="777", title="Calc", price=None, calculated=True)])

        result = await router.refresh_shipping_prices(ETSY, session=session)

        assert [c.name for c in result.changed] == ["Small parcel"]
        assert Decimal(profile.price_etsy) == Decimal("3.60")
        assert result.unchanged_count == 0 and result.error is None
        await session.refresh(conn)
        assert conn.last_shipping_price_refresh_at.replace(tzinfo=None) == result.refreshed_at.replace(tzinfo=None)

    async def test_the_manual_endpoint_reports_a_rate_limit_rather_than_failing(self, session, marketplace):
        await _connect(session)
        await _profile(session, etsy_shipping_profile_id=501)
        marketplace(error=PlatformRateLimitError("slow down"))
        result = await router.refresh_shipping_prices(ETSY, session=session)
        assert result.error == "slow down" and result.changed == []

    async def test_price_history_is_newest_first(self, session, marketplace):
        profile = await _profile(session, etsy_shipping_profile_id=501, price_etsy=Decimal("1.00"))
        await _connect(session)
        marketplace([_mp(price="2.00")])
        await shipping_price_sync.refresh(session, ETSY)
        marketplace([_mp(price="3.00")])
        await shipping_price_sync.refresh(session, ETSY)

        history = await router.list_shipping_profile_price_events(profile.id, session=session)
        assert [Decimal(e.new_price) for e in history] == [Decimal("3.00"), Decimal("2.00")]

    async def test_refresh_status_lists_both_platforms(self, session):
        await _connect(session, shipping_price_refresh_hours=12)
        await _profile(session, etsy_shipping_profile_id=501)
        rows = await router.get_price_refresh_status(session=session)
        by_platform = {r.platform: r for r in rows}
        assert by_platform[ETSY].connected is True
        assert by_platform[ETSY].linked_count == 1
        assert by_platform[ETSY].shipping_price_refresh_hours == 12
        assert by_platform[EBAY].connected is False
