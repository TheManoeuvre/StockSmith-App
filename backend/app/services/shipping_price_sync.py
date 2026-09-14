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
  * `refresh` (Stage 3) — the scheduled sweep run from the order-sync tick, one fetch per
    platform matched to every linked profile in memory.

Linking alone imports nothing: a link is a statement about identity, and the import is a
separate, visible act (or the scheduled refresh) so a price never changes on the way past.

Calculated marketplace profiles (Etsy profile_type "calculated", eBay costType
"CALCULATED") have no fixed price; they are reported as such and nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.shipping_profile import ShippingProfile
from app.services import listing_profiles
from app.services.platforms import get_adapter

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
        default_profile = await listing_profiles.get_default_profile(session, platform)
        marketplace_id = (default_profile.ebay_marketplace_id if default_profile else None) or _DEFAULT_EBAY_MARKETPLACE_ID
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
    at nothing), or the adapter's PlatformError. Commits on success; the caller records
    the change (see record_price_change) so the provenance label is the caller's."""
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
        await session.commit()
        await session.refresh(profile)
    return ImportResult(profile=profile, marketplace=match, old_price=old_price, new_price=new_price)
