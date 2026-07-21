from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.product import Product
from app.schemas.dashboard import BuildableProduct, DashboardSummary
from app.services.buildability import compute_dashboard_summary, get_max_buildable_by_product

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(require_auth)])


@router.get("/summary", response_model=DashboardSummary)
async def summary(session: AsyncSession = Depends(get_db)) -> DashboardSummary:
    return await compute_dashboard_summary(session)


@router.get("/max-buildable", response_model=list[BuildableProduct])
async def max_buildable(session: AsyncSession = Depends(get_db)) -> list[BuildableProduct]:
    max_buildable_by_product = await get_max_buildable_by_product(session)
    result = await session.execute(select(Product).where(Product.is_active.is_(True)))
    products = list(result.scalars())
    return [
        BuildableProduct(product_id=p.id, name=p.name, max_buildable=max_buildable_by_product.get(p.id))
        for p in products
    ]
