"""Idempotent seed data for a fresh database.

Previously these rows were inserted directly by their originating Alembic migrations
(a4c8e2f6b193, f7a2c8e1b4d9). Moved here so seed data isn't baked into schema-versioning
migrations that could be squashed again later — this runs once at startup (see
app/bootstrap.py) and is a no-op on every run after the first.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backup_settings import BackupSettings
from app.models.general_settings import CurrencyCode, GeneralSettings
from app.models.material_category import MaterialCategory
from app.models.material_unit import MaterialUnitRow
from app.models.notification import ALERT_TYPES, DEFAULT_IMMEDIATE_ALERT_TYPES, NotificationDeliveryMode, NotificationSettings, NotificationTypeSettings
from app.models.platform_fee import FeeBasis, MarginFeeConfig, MarginFeeSource, PlatformFeeComponent
from app.models.shipping_profile import ShippingProfile
from app.services.material_categories import DEFAULT_CATEGORIES
from app.services.units import DEFAULT_UNITS

# Etsy UK / eBay UK fee components as researched July 2026 — point-in-time rates,
# periodically re-verify against each platform's own fee pages.
_FEE_COMPONENTS: list[dict] = [
    {
        "platform": "etsy",
        "name": "Transaction fee",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 6.5,
        "fixed_amount": None,
        "display_order": 1,
        "enabled": True,
    },
    {
        "platform": "etsy",
        "name": "Payment processing",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 4.0,
        "fixed_amount": 0.20,
        "display_order": 2,
        "enabled": True,
    },
    {
        "platform": "etsy",
        "name": "Regulatory operating fee",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 0.48,
        "fixed_amount": None,
        "display_order": 3,
        "enabled": True,
    },
    {
        "platform": "etsy",
        "name": "VAT on fees",
        "basis": FeeBasis.fees_subtotal,
        "rate_percent": 20.0,
        "fixed_amount": None,
        "display_order": 4,
        "enabled": True,
    },
    {
        "platform": "etsy",
        "name": "Offsite ads (situational)",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 12.0,
        "fixed_amount": None,
        "display_order": 5,
        "enabled": False,
    },
    # eBay UK — final value fee is category-dependent (~6.9-14.9%); 12.8% is the common
    # business-seller rate, seeded as a representative default. Per-order fee is tiered
    # by order value (30p at or below £10, 40p above) — seeded with the lower tier.
    {
        "platform": "ebay",
        "name": "Final value fee",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 12.8,
        "fixed_amount": None,
        "display_order": 1,
        "enabled": True,
    },
    {
        "platform": "ebay",
        "name": "Per-order fee",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": None,
        "fixed_amount": 0.30,
        "display_order": 2,
        "enabled": True,
    },
    {
        "platform": "ebay",
        "name": "Regulatory operating fee",
        "basis": FeeBasis.sale_price_plus_shipping,
        "rate_percent": 0.35,
        "fixed_amount": None,
        "display_order": 3,
        "enabled": True,
    },
    {
        "platform": "ebay",
        "name": "VAT on fees",
        "basis": FeeBasis.fees_subtotal,
        "rate_percent": 20.0,
        "fixed_amount": None,
        "display_order": 4,
        "enabled": True,
    },
]


# Royal Mail 2nd Class, UK. Stamp (Post Office counter) prices from the 5 October 2026 change —
# point-in-time, like the fees above. RE-CHECK QUARTERLY: see docs/plan-onboarding.md §12, which
# records when these were last verified. Stamp prices rather than online (Click & Drop) prices
# because they're the figures every source agrees on; online is usually cheaper, and the help
# tells users to replace these with what they actually pay.
#
# Seeded as cost on every channel *and* as the buyer price — postage passed on at cost — so a
# new install's margins neither invent postage profit nor ignore postage cost. UK-specific: a
# seller elsewhere archives them.
ROYAL_MAIL_VERIFIED = "2026-11-19"
_SHIPPING_PROFILES: list[tuple[str, str]] = [
    ("Royal Mail 2nd Class — Large Letter (up to 100g)", "1.55"),
    ("Royal Mail 2nd Class — Small Parcel (up to 2kg)", "4.60"),
    ("Royal Mail 2nd Class — Medium Parcel (up to 2kg)", "7.50"),
]


async def _ensure_general_settings(session: AsyncSession) -> None:
    existing = await session.execute(select(GeneralSettings).where(GeneralSettings.id == 1))
    if existing.scalar_one_or_none() is None:
        session.add(GeneralSettings(id=1, default_currency=CurrencyCode.GBP))


async def _ensure_backup_settings(session: AsyncSession) -> None:
    existing = await session.execute(select(BackupSettings).where(BackupSettings.id == 1))
    if existing.scalar_one_or_none() is None:
        # Scheduling on by default — see the model docstring. An opt-in backup is the one
        # nobody turned on.
        session.add(BackupSettings(id=1))


async def _ensure_notification_settings(session: AsyncSession) -> None:
    existing = await session.execute(select(NotificationSettings).where(NotificationSettings.id == 1))
    if existing.scalar_one_or_none() is None:
        session.add(NotificationSettings(id=1))


async def _ensure_notification_type_settings(session: AsyncSession) -> None:
    """Seeds all 9 alert types — immediate for the ones that most directly need a human's
    attention right away, digest for the rest. See DEFAULT_IMMEDIATE_ALERT_TYPES."""
    existing = await session.execute(select(NotificationTypeSettings.alert_type))
    existing_types = {row.alert_type for row in existing}
    for alert_type in ALERT_TYPES:
        if alert_type in existing_types:
            continue
        session.add(
            NotificationTypeSettings(
                alert_type=alert_type,
                enabled=True,
                delivery_mode=(
                    NotificationDeliveryMode.immediate
                    if alert_type in DEFAULT_IMMEDIATE_ALERT_TYPES
                    else NotificationDeliveryMode.digest
                ),
            )
        )


async def _ensure_margin_fee_config(session: AsyncSession) -> None:
    existing = await session.execute(select(MarginFeeConfig).where(MarginFeeConfig.id == 1))
    if existing.scalar_one_or_none() is None:
        session.add(MarginFeeConfig(id=1, fee_source=MarginFeeSource.manual))


async def _ensure_platform_fee_components(session: AsyncSession) -> None:
    existing = await session.execute(select(PlatformFeeComponent.id).limit(1))
    if existing.first() is not None:
        return
    for row in _FEE_COMPONENTS:
        session.add(PlatformFeeComponent(**row))


async def _ensure_material_categories(session: AsyncSession) -> None:
    """Seed the seven original categories into a database that has none.

    Duplicated from the migration that introduced the table, and deliberately so. The migration
    needs the rows in its own transaction because its backfill matches materials against them;
    this needs to exist because the test suite builds its schema with create_all and never runs
    migrations, and because a future baseline squash would otherwise drop them on the floor.

    Guarded on the table being empty rather than upserting row by row. Categories are editable
    now — someone who deleted "pigment" must not find it back after the next restart.
    """
    existing = await session.execute(select(MaterialCategory.id).limit(1))
    if existing.first() is not None:
        return
    for row in DEFAULT_CATEGORIES:
        session.add(MaterialCategory(**row))


async def _ensure_material_units(session: AsyncSession) -> None:
    """Seed g, ml and each into a database that has no units. Same reasoning as the categories:
    the migration seeds its own copy; this covers create_all test databases, and is guarded on
    the table being empty so a unit someone deleted does not come back on the next restart."""
    existing = await session.execute(select(MaterialUnitRow.id).limit(1))
    if existing.first() is not None:
        return
    for row in DEFAULT_UNITS:
        session.add(MaterialUnitRow(**row))


async def _ensure_shipping_profiles(session: AsyncSession) -> None:
    """Seed the Royal Mail starter set into a brand-new install only: no shipping profiles, and no
    business type chosen yet (existing installs were stamped with one by migration d2e7a4c19f60,
    so one that simply never set up postage doesn't find these appear after an update)."""
    settings = await session.get(GeneralSettings, 1)
    if settings is None or settings.business_type is not None:
        return
    existing = await session.execute(select(ShippingProfile.id).limit(1))
    if existing.first() is not None:
        return
    for name, amount in _SHIPPING_PROFILES:
        value = Decimal(amount)
        session.add(ShippingProfile(name=name, price=value, cost_manual=value, cost_etsy=value, cost_ebay=value))


async def ensure_seed_data(session: AsyncSession) -> None:
    """Idempotent — safe to call on every startup, only inserts what's missing."""
    await _ensure_general_settings(session)
    await _ensure_backup_settings(session)
    await _ensure_notification_settings(session)
    await _ensure_notification_type_settings(session)
    await _ensure_margin_fee_config(session)
    await _ensure_platform_fee_components(session)
    await _ensure_material_units(session)
    await _ensure_material_categories(session)
    await _ensure_shipping_profiles(session)
    await session.commit()
