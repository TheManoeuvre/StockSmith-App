from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.kitting import DefaultKittingMaterial
from app.models.listing import ListingPlatform
from app.models.platform_fee import PlatformFeeComponent
from app.schemas.abc import StockCountSettingsRead, StockCountSettingsUpdate
from app.schemas.kitting import DefaultKittingBomLineRead, KittingBomLine
from app.schemas.platform_fee import (
    DefaultCurrencyRead,
    DefaultCurrencyUpdate,
    ForecastSettingsRead,
    ForecastSettingsUpdate,
    MarginFeeConfigRead,
    MarginFeeConfigUpdate,
    PlatformFeeComponentCreate,
    PlatformFeeComponentRead,
    PlatformFeeComponentUpdate,
)
from app.services import abc, general_settings, platform_fees
from app.services.kitting import get_default_kitting_bom, replace_default_kitting_bom
from app.services.validation import validate_lines_against_units

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("/default-currency", response_model=DefaultCurrencyRead)
async def get_default_currency(session: AsyncSession = Depends(get_db)) -> DefaultCurrencyRead:
    settings = await general_settings.get_general_settings(session)
    return DefaultCurrencyRead(default_currency=settings.default_currency)


@router.put("/default-currency", response_model=DefaultCurrencyRead)
async def update_default_currency(
    payload: DefaultCurrencyUpdate, session: AsyncSession = Depends(get_db)
) -> DefaultCurrencyRead:
    settings = await general_settings.set_default_currency(session, payload.default_currency)
    return DefaultCurrencyRead(default_currency=settings.default_currency)


@router.get("/stock-count-settings", response_model=StockCountSettingsRead)
async def get_stock_count_settings(session: AsyncSession = Depends(get_db)) -> StockCountSettingsRead:
    return await abc.read_settings(session)


@router.put("/stock-count-settings", response_model=StockCountSettingsRead)
async def update_stock_count_settings(
    payload: StockCountSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> StockCountSettingsRead:
    return await abc.write_settings(session, payload)


@router.get("/forecast-settings", response_model=ForecastSettingsRead)
async def get_forecast_settings(session: AsyncSession = Depends(get_db)) -> ForecastSettingsRead:
    settings = await general_settings.get_general_settings(session)
    return ForecastSettingsRead(
        forecast_warning_weeks=settings.forecast_warning_weeks,
        forecast_critical_weeks=settings.forecast_critical_weeks,
        forecast_lookback_weeks=settings.forecast_lookback_weeks,
        default_lead_time_weeks=settings.default_lead_time_weeks,
    )


@router.put("/forecast-settings", response_model=ForecastSettingsRead)
async def update_forecast_settings(
    payload: ForecastSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> ForecastSettingsRead:
    if payload.forecast_critical_weeks > payload.forecast_warning_weeks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Critical threshold must be at or below the warning threshold",
        )
    settings = await general_settings.set_forecast_settings(
        session,
        payload.forecast_warning_weeks,
        payload.forecast_critical_weeks,
        payload.forecast_lookback_weeks,
        payload.default_lead_time_weeks,
    )
    return ForecastSettingsRead(
        forecast_warning_weeks=settings.forecast_warning_weeks,
        forecast_critical_weeks=settings.forecast_critical_weeks,
        forecast_lookback_weeks=settings.forecast_lookback_weeks,
        default_lead_time_weeks=settings.default_lead_time_weeks,
    )


@router.get("/default-kitting-bom", response_model=list[DefaultKittingBomLineRead])
async def get_default_kitting_bom_route(session: AsyncSession = Depends(get_db)) -> list[DefaultKittingMaterial]:
    return await get_default_kitting_bom(session)


@router.put("/default-kitting-bom", response_model=list[DefaultKittingBomLineRead])
async def update_default_kitting_bom(
    payload: list[KittingBomLine], session: AsyncSession = Depends(get_db)
) -> list[DefaultKittingMaterial]:
    await validate_lines_against_units(
        session, [(l.material_id, l.qty_required) for l in payload], "qty_required"
    )
    return await replace_default_kitting_bom(session, [(l.material_id, l.qty_required) for l in payload])


@router.get("/margin-fee-config", response_model=MarginFeeConfigRead)
async def get_margin_fee_config(session: AsyncSession = Depends(get_db)) -> MarginFeeConfigRead:
    config = await platform_fees.get_margin_fee_config(session)
    return MarginFeeConfigRead(fee_source=config.fee_source)


@router.put("/margin-fee-config", response_model=MarginFeeConfigRead)
async def update_margin_fee_config(
    payload: MarginFeeConfigUpdate, session: AsyncSession = Depends(get_db)
) -> MarginFeeConfigRead:
    config = await platform_fees.set_margin_fee_config(session, payload.fee_source)
    return MarginFeeConfigRead(fee_source=config.fee_source)


@router.get("/platform-fee-components/{platform}", response_model=list[PlatformFeeComponentRead])
async def list_platform_fee_components(
    platform: ListingPlatform, session: AsyncSession = Depends(get_db)
) -> list[PlatformFeeComponent]:
    return await platform_fees.get_fee_components(session, platform)


@router.post(
    "/platform-fee-components/{platform}", response_model=PlatformFeeComponentRead, status_code=status.HTTP_201_CREATED
)
async def create_platform_fee_component(
    platform: ListingPlatform, payload: PlatformFeeComponentCreate, session: AsyncSession = Depends(get_db)
) -> PlatformFeeComponent:
    component = PlatformFeeComponent(platform=platform, **payload.model_dump())
    session.add(component)
    await session.commit()
    await session.refresh(component)
    return component


@router.patch("/platform-fee-components/{platform}/{component_id}", response_model=PlatformFeeComponentRead)
async def update_platform_fee_component(
    platform: ListingPlatform,
    component_id: int,
    payload: PlatformFeeComponentUpdate,
    session: AsyncSession = Depends(get_db),
) -> PlatformFeeComponent:
    component = await session.get(PlatformFeeComponent, component_id)
    if component is None or component.platform != platform:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee component not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(component, field, value)
    await session.commit()
    await session.refresh(component)
    return component


@router.delete("/platform-fee-components/{platform}/{component_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform_fee_component(
    platform: ListingPlatform, component_id: int, session: AsyncSession = Depends(get_db)
) -> None:
    component = await session.get(PlatformFeeComponent, component_id)
    if component is None or component.platform != platform:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee component not found")
    await session.delete(component)
    await session.commit()
