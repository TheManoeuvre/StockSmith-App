from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.platform_fee import FeeBasis, MarginFeeSource


class PlatformFeeComponentCreate(BaseModel):
    name: str
    basis: FeeBasis
    rate_percent: Decimal | None = None
    fixed_amount: Decimal | None = None
    display_order: int = 0
    enabled: bool = True


class PlatformFeeComponentUpdate(BaseModel):
    name: str | None = None
    basis: FeeBasis | None = None
    rate_percent: Decimal | None = None
    fixed_amount: Decimal | None = None
    display_order: int | None = None
    enabled: bool | None = None


class PlatformFeeComponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    name: str
    basis: FeeBasis
    rate_percent: Decimal | None
    fixed_amount: Decimal | None
    display_order: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class MarginFeeConfigRead(BaseModel):
    fee_source: MarginFeeSource


class MarginFeeConfigUpdate(BaseModel):
    fee_source: MarginFeeSource
