from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.listing import ListingPlatform
from app.models.platform_fee import PlatformFeeComponent
from app.schemas.platform_fee import (
    DefaultCurrencyRead,
    DefaultCurrencyUpdate,
    MarginFeeConfigRead,
    MarginFeeConfigUpdate,
    PlatformFeeComponentCreate,
    PlatformFeeComponentRead,
    PlatformFeeComponentUpdate,
)
from app.services import general_settings, platform_fees

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
