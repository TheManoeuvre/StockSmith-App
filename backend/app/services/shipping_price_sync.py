"""Keeping ShippingProfile.price_<platform> in step with what the marketplace charges.

A shipping profile linked to an Etsy shipping profile or an eBay fulfillment policy
(ShippingProfile.etsy_shipping_profile_id / ebay_fulfillment_policy_id) has a buyer price
the marketplace owns: the seller sets it in the marketplace's own editor, and product margin
counts it as revenue (pricing.compute_profit_margin). This module is the one place that
reads it back.

Two entry points, both writing only price_<platform> — never `price` (the manual/default
figure) and never any cost_* (what the carrier charges the seller, which the marketplace
knows nothing about):

  * `import_price` — the explicit per-profile "Pull price from Etsy/eBay" action. Fetches
    the linked marketplace profile and writes its domestic buyer price.
  * `refresh` — the scheduled sweep run from the end of a successful background order-sync
    tick (sync_scheduler._tick → refresh_if_due), at most once per
    PlatformConnection.shipping_price_refresh_hours: one fetch per platform, matched to
    every linked profile in memory. Also what the manual "Refresh from Etsy/eBay" button
    runs. It overwrites, records and alerts rather than asking: the marketplace is the
    source of truth, and every change lands in shipping_profile_price_events (see
    record_price_change) so "why did margin drop on the 3rd" has an answer.

Linking alone imports nothing: a link is a statement about identity, and the import is a
separate, visible act (or the scheduled refresh) so a price never changes on the way past.

Calculated marketplace profiles (Etsy profile_type "calculated", eBay costType
"CALCULATED") have no fixed price; they are reported as such and nothing is written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_factory
from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.shipping_profile import PriceEventSource, ShippingProfile, ShippingProfilePriceEvent
from app.services import listing_profiles, notification_alerts
from app.services.platforms import get_adapter
from app.services.platforms.errors import PlatformAuthError, PlatformRateLimitError, PlatformSyncError

logger = logging.getLogger("stocksmith.shipping_price_sync")

# eBay business policies are per marketplace. Used when no eBay listing profile names one.
_DEFAULT_EBAY_MARKETPLACE_ID = "EBAY_GB"

PLATFORM_LABELS = {ListingPlatform.etsy: "Etsy", ListingPlatform.ebay: "eBay"}


class NotConnectedError(Exception):
    """The platform has no live connection, so nothing can be fetched."""


class NotLinkedError(Exception):
    """The local profile carries no marketplace id for this platform."""


class CalculatedProfileError(Exception):
    """The marketplace works postage out per buyer at checkout — there is no fixed price
    to import. Carries a sentence meant for the user."""


class MissingUpstreamError(Exception):
    """The link points at a marketplace profile that no longer exists (deleted on Etsy,
    or absent from eBay's policy list)."""


@dataclass
class MarketplaceProfile:
    """One marketplace shipping profile / fulfillment policy in neutral terms — the shape
    the link picker, the import and the refresh all consume."""

    id: str
    title: str
    is_calculated: bool
    domestic_price: Decimal | None
    domestic_fallback: bool


def link_id(profile: ShippingProfile, platform: ListingPlatform) -> str | None:
    """The marketplace id this local profile is linked to on `platform`, as a string (Etsy's
    are numeric, eBay's opaque; the comparison key is the string either way)."""
    if platform == ListingPlatform.etsy:
        return str(profile.etsy_shipping_profile_id) if profile.etsy_shipping_profile_id is not None else None
    if platform == ListingPlatform.ebay:
        return profile.ebay_fulfillment_policy_id or None
    return None


def price_attribute(platform: ListingPlatform) -> str:
    return f"price_{platform.value}"


async def get_connection(session: AsyncSession, platform: ListingPlatform) -> PlatformConnection:
    result = await session.execute(select(PlatformConnection).where(PlatformConnection.platform == platform))
    connection = result.scalar_one_or_none()
    if connection is None or not connection.is_connected:
        raise NotConnectedError(f"{PLATFORM_LABELS.get(platform, platform.value)} is not connected")
    return connection


async def fetch_marketplace_profiles(session: AsyncSession, platform: ListingPlatform) -> list[MarketplaceProfile]:
    """One call to the marketplace for every shipping profile / postage policy it holds.

    Raises NotConnectedError, or whatever PlatformError the adapter raises — callers map
    those to the reconnect-required / rate-limit / gateway statuses they already know."""
    connection = await get_connection(session, platform)
    adapter = await get_adapter(session, platform)
    if platform == ListingPlatform.etsy:
        raw = await adapter.fetch_shipping_profiles(session, connection)
        return [
            MarketplaceProfile(
                id=str(p["id"]),
                title=p["title"],
                is_calculated=p.get("profile_type") == "calculated",
                domestic_price=p.get("domestic_price"),
                domestic_fallback=bool(p.get("domestic_fallback")),
            )
            for p in raw
            if p.get("id") is not None
        ]
    if platform == ListingPlatform.ebay:
        # Business policies are per marketplace. A shop lists on one, so any profile that
        # names it will do; EBAY_GB when none does.
        marketplace_id = next(
            (p.ebay_marketplace_id for p in await listing_profiles.list_profiles(session, platform) if p.ebay_marketplace_id),
            _DEFAULT_EBAY_MARKETPLACE_ID,
        )
        raw = await adapter.fetch_fulfillment_policies(session, connection, marketplace_id)
        return [
            MarketplaceProfile(
                id=str(p["id"]),
                title=p["title"],
                is_calculated=p.get("cost_type") == "CALCULATED",
                domestic_price=p.get("domestic_price"),
                domestic_fallback=bool(p.get("domestic_fallback")),
            )
            for p in raw
            if p.get("id") is not None
        ]
    raise NotConnectedError(f"{platform.value} has no shipping profile integration")


@dataclass
class ImportResult:
    profile: ShippingProfile
    marketplace: MarketplaceProfile
    old_price: Decimal | None
    new_price: Decimal | None

    @property
    def changed(self) -> bool:
        return self.old_price != self.new_price


async def import_price(session: AsyncSession, profile: ShippingProfile, platform: ListingPlatform) -> ImportResult:
    """Writes price_<platform> from the linked marketplace profile's domestic buyer price.

    Raises NotLinkedError (no link for this platform), NotConnectedError,
    CalculatedProfileError (nothing fixed to import), MissingUpstreamError (the link points
    at nothing), or the adapter's PlatformError. Commits on success and records the
    change as a manual_import event (see record_price_change)."""
    marketplace_id = link_id(profile, platform)
    if marketplace_id is None:
        raise NotLinkedError(f"'{profile.name}' is not linked to a {PLATFORM_LABELS[platform]} profile")

    candidates = await fetch_marketplace_profiles(session, platform)
    match = next((c for c in candidates if c.id == marketplace_id), None)
    if match is None:
        raise MissingUpstreamError(
            f"The {PLATFORM_LABELS[platform]} profile '{profile.name}' is linked to no longer exists "
            f"(id {marketplace_id}). Re-link it to a current profile."
        )
    if match.is_calculated:
        raise CalculatedProfileError(
            f"'{match.title}' is a calculated profile on {PLATFORM_LABELS[platform]} — postage is worked out per "
            "buyer at checkout, so there is no fixed price to import."
        )

    attribute = price_attribute(platform)
    old = getattr(profile, attribute)
    old_price = Decimal(old) if old is not None else None
    new_price = match.domestic_price
    if new_price is not None and new_price != old_price:
        setattr(profile, attribute, new_price)
        await record_price_change(session, profile, platform, old_price, new_price, PriceEventSource.manual_import)
        await session.commit()
        await session.refresh(profile)
    return ImportResult(profile=profile, marketplace=match, old_price=old_price, new_price=new_price)


# --- Recording ---------------------------------------------------------------------------


async def record_price_change(
    session: AsyncSession,
    profile: ShippingProfile,
    platform: ListingPlatform,
    old_price: Decimal | None,
    new_price: Decimal | None,
    source: PriceEventSource,
) -> ShippingProfilePriceEvent | None:
    """Appends one shipping_profile_price_events row when the price actually moved. Every
    writer of price_<platform> calls this — the refresh, the manual import and the Settings
    edit — so the log is complete rather than sync-only. Does not commit; the caller owns
    the transaction the price change is in."""
    if old_price == new_price:
        return None
    event = ShippingProfilePriceEvent(
        shipping_profile_id=profile.id,
        platform=platform,
        old_price=old_price,
        new_price=new_price,
        source=source,
    )
    session.add(event)
    return event


def _as_decimal(value) -> Decimal | None:
    return Decimal(value) if value is not None else None


# --- Scheduled refresh ---------------------------------------------------------------------


@dataclass
class PriceChange:
    profile: ShippingProfile
    old_price: Decimal | None
    new_price: Decimal | None


@dataclass
class RefreshResult:
    platform: ListingPlatform
    changed: list[PriceChange] = field(default_factory=list)
    unchanged: list[ShippingProfile] = field(default_factory=list)
    skipped_calculated: list[ShippingProfile] = field(default_factory=list)
    missing_upstream: list[ShippingProfile] = field(default_factory=list)
    # Set when the marketplace couldn't be read (rate limit, sync error): nothing was
    # written and, for a rate limit, the connection's timestamp was left alone so the
    # next tick retries.
    error: str | None = None
    refreshed_at: datetime | None = None


async def _linked_profiles(session: AsyncSession, platform: ListingPlatform) -> list[ShippingProfile]:
    """Active profiles linked to this platform. Archived ones keep their link but are
    retired — refreshing a price nobody uses, or alerting that it vanished, is noise."""
    if platform == ListingPlatform.etsy:
        condition = ShippingProfile.etsy_shipping_profile_id.is_not(None)
    elif platform == ListingPlatform.ebay:
        condition = ShippingProfile.ebay_fulfillment_policy_id.is_not(None)
    else:
        return []
    result = await session.execute(
        select(ShippingProfile).where(condition, ShippingProfile.is_archived.is_(False)).order_by(ShippingProfile.name)
    )
    return list(result.scalars())


def is_refresh_due(connection: PlatformConnection, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = connection.last_shipping_price_refresh_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(hours=max(connection.shipping_price_refresh_hours, 1))


async def refresh(
    session: AsyncSession, platform: ListingPlatform, *, source: PriceEventSource = PriceEventSource.sync
) -> RefreshResult:
    """One fetch of the marketplace's shipping profiles, matched to every linked local
    profile; each changed price is written to price_<platform>, recorded and reported.

    Overwrite, record, alert — never ask: the marketplace is the source of truth for what
    the buyer pays. A linked profile that vanished upstream keeps its last price and is
    flagged (drafts pushed with that id will fail); calculated marketplace profiles are
    skipped silently since they have no fixed price.

    Error handling by kind, matching the order-sync loop this rides on:
      - PlatformAuthError propagates — the sync loop's consecutive-failure counter owns
        auth state, and its order sync in the same tick failed the same way.
      - PlatformRateLimitError: nothing written, the connection's timestamp is left unset
        so the next tick retries, reported in `error`.
      - Any other PlatformSyncError: nothing written, reported, and the timestamp *is*
        advanced so a persistently broken read costs one call per refresh window rather
        than one per tick. The manual button surfaces the message directly.
    Commits."""
    result = RefreshResult(platform=platform)
    connection = await get_connection(session, platform)
    profiles = await _linked_profiles(session, platform)
    now = datetime.now(timezone.utc)

    try:
        candidates = {c.id: c for c in await fetch_marketplace_profiles(session, platform)} if profiles else {}
    except PlatformRateLimitError as e:
        result.error = str(e)
        logger.warning("Shipping price refresh for %s skipped — rate limited: %s", platform.value, e)
        return result
    except PlatformAuthError:
        raise
    except PlatformSyncError as e:
        result.error = str(e)
        logger.warning("Shipping price refresh for %s failed: %s", platform.value, e)
        connection.last_shipping_price_refresh_at = now
        await session.commit()
        return result

    attribute = price_attribute(platform)
    present: list[ShippingProfile] = []
    for profile in profiles:
        match = candidates.get(link_id(profile, platform) or "")
        if match is None:
            result.missing_upstream.append(profile)
            continue
        present.append(profile)
        if match.is_calculated or match.domestic_price is None:
            result.skipped_calculated.append(profile)
            continue
        old_price = _as_decimal(getattr(profile, attribute))
        if match.domestic_price == old_price:
            result.unchanged.append(profile)
            continue
        setattr(profile, attribute, match.domestic_price)
        await record_price_change(session, profile, platform, old_price, match.domestic_price, source)
        result.changed.append(PriceChange(profile=profile, old_price=old_price, new_price=match.domestic_price))

    connection.last_shipping_price_refresh_at = now
    result.refreshed_at = now
    await session.commit()
    for profile in profiles:
        await session.refresh(profile)

    await notification_alerts.raise_shipping_price_changed_alert(
        session, platform, [(c.profile.name, c.old_price, c.new_price) for c in result.changed]
    )
    await notification_alerts.check_shipping_profile_missing_alerts(session, platform, result.missing_upstream, present)
    return result


async def refresh_if_due(platform: ListingPlatform) -> RefreshResult | None:
    """The scheduler hook: run from sync_scheduler._tick after a successful order sync,
    under the platform lock. Opens its own short-lived session (the tick holds none) and
    returns None when the refresh window hasn't elapsed or the platform isn't connected.
    PlatformAuthError propagates to the tick's existing handler; see refresh."""
    async with async_session_factory() as session:
        try:
            connection = await get_connection(session, platform)
        except NotConnectedError:
            return None
        if not is_refresh_due(connection):
            return None
        result = await refresh(session, platform)
        if result.changed:
            logger.info(
                "Refreshed %s shipping prices: %d changed, %d unchanged, %d missing upstream",
                platform.value,
                len(result.changed),
                len(result.unchanged),
                len(result.missing_upstream),
            )
        return result
