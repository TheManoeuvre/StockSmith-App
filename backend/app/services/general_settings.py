from sqlalchemy.ext.asyncio import AsyncSession

from app.models.general_settings import CurrencyCode, GeneralSettings

# Display-only symbol for each supported currency — matches frontend/src/lib/money.ts's
# formatMoney mapping, since the two must agree on what a figure in that currency looks
# like.
CURRENCY_SYMBOLS: dict[CurrencyCode, str] = {
    CurrencyCode.GBP: "£",
    CurrencyCode.EUR: "€",
    CurrencyCode.USD: "$",
}


async def get_general_settings(session: AsyncSession) -> GeneralSettings:
    settings = await session.get(GeneralSettings, 1)
    if settings is None:
        # Should only happen on a DB that predates the seeding migration somehow — fall
        # back to the same safe default the migration seeds.
        settings = GeneralSettings(id=1, default_currency=CurrencyCode.GBP)
        session.add(settings)
        await session.commit()
    return settings


async def get_default_currency_symbol(session: AsyncSession) -> str:
    settings = await get_general_settings(session)
    return CURRENCY_SYMBOLS[settings.default_currency]


async def set_default_currency(session: AsyncSession, default_currency: CurrencyCode) -> GeneralSettings:
    settings = await get_general_settings(session)
    settings.default_currency = default_currency
    await session.commit()
    return settings


async def set_forecast_settings(
    session: AsyncSession,
    forecast_warning_weeks,
    forecast_critical_weeks,
    forecast_lookback_weeks: int,
    default_lead_time_days: int,
) -> GeneralSettings:
    settings = await get_general_settings(session)
    settings.forecast_warning_weeks = forecast_warning_weeks
    settings.forecast_critical_weeks = forecast_critical_weeks
    settings.forecast_lookback_weeks = forecast_lookback_weeks
    settings.default_lead_time_days = default_lead_time_days
    await session.commit()
    return settings
