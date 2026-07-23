from sqlalchemy.ext.asyncio import AsyncSession

from app.models.general_settings import CurrencyCode, GeneralSettings


async def get_general_settings(session: AsyncSession) -> GeneralSettings:
    settings = await session.get(GeneralSettings, 1)
    if settings is None:
        # Should only happen on a DB that predates the seeding migration somehow — fall
        # back to the same safe default the migration seeds.
        settings = GeneralSettings(id=1, default_currency=CurrencyCode.GBP)
        session.add(settings)
        await session.commit()
    return settings


async def set_default_currency(session: AsyncSession, default_currency: CurrencyCode) -> GeneralSettings:
    settings = await get_general_settings(session)
    settings.default_currency = default_currency
    await session.commit()
    return settings
