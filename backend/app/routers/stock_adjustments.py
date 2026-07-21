from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.stock_adjustment import StockAdjustment
from app.schemas.stock_adjustment import StockAdjustmentCreate, StockAdjustmentRead
from app.services.stock_adjustments import create_stock_adjustment

router = APIRouter(prefix="/stock-adjustments", tags=["stock-adjustments"], dependencies=[Depends(require_auth)])


@router.post("", response_model=StockAdjustmentRead, status_code=status.HTTP_201_CREATED)
async def record_stock_adjustment(
    payload: StockAdjustmentCreate, session: AsyncSession = Depends(get_db)
) -> StockAdjustment:
    return await create_stock_adjustment(
        session, payload.product_id, payload.variant_id, payload.mode, payload.value, payload.reason
    )
