"""Idempotent seed data for a fresh database.

Previously these rows were inserted directly by their originating Alembic migrations
(a4c8e2f6b193, f7a2c8e1b4d9). Moved here so seed data isn't baked into schema-versioning
migrations that could be squashed again later — this runs once at startup (see
app/bootstrap.py) and is a no-op on every run after the first.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.general_settings import CurrencyCode, GeneralSettings
from app.models.platform_fee import FeeBasis, MarginFeeConfig, MarginFeeSource, PlatformFeeComponent

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


async def _ensure_general_settings(session: AsyncSession) -> None:
    existing = await session.execute(select(GeneralSettings).where(GeneralSettings.id == 1))
    if existing.scalar_one_or_none() is None:
        session.add(GeneralSettings(id=1, default_currency=CurrencyCode.GBP))


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


async def ensure_seed_data(session: AsyncSession) -> None:
    """Idempotent — safe to call on every startup, only inserts what's missing."""
    await _ensure_general_settings(session)
    await _ensure_margin_fee_config(session)
    await _ensure_platform_fee_components(session)
    await session.commit()
