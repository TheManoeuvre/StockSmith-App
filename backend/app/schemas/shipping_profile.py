from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ShippingProfileBase(BaseModel):
    name: str
    price: Decimal = Decimal(0)
    cost_etsy: Decimal = Decimal(0)
    cost_ebay: Decimal = Decimal(0)
    cost_manual: Decimal = Decimal(0)


class ShippingProfileCreate(ShippingProfileBase):
    pass


class ShippingProfileUpdate(BaseModel):
    name: str | None = None
    price: Decimal | None = None
    cost_etsy: Decimal | None = None
    cost_ebay: Decimal | None = None
    cost_manual: Decimal | None = None


class ShippingProfileRead(ShippingProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
