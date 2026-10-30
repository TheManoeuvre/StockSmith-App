from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.schemas.insights import InsightsCosts, InsightsOverview, InsightsProducts, InsightsQuality
from app.services import insights
from app.services import insights_breakdown as breakdown

router = APIRouter(prefix="/insights", tags=["insights"], dependencies=[Depends(require_auth)])


def _check_range(start: date, end: date, label: str = "") -> None:
    if end < start:
        raise HTTPException(status_code=422, detail=f"{label}end must not be before start")
    if (end - start).days > insights.MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"{label}range is limited to {insights.MAX_RANGE_DAYS} days")


def _parse_channels(channels: str | None) -> set[str] | None:
    """Comma-separated, e.g. "etsy,ebay". Omitted means every channel."""
    if not channels:
        return None
    wanted = {c.strip().lower() for c in channels.split(",") if c.strip()}
    unknown = wanted - set(insights.CHANNELS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown channel(s): {', '.join(sorted(unknown))}")
    return wanted


@router.get("/overview", response_model=InsightsOverview)
async def overview(
    start: date,
    end: date,
    granularity: str = Query("week"),
    channels: str | None = None,
    include_failed_builds: bool = True,
    compare_start: date | None = None,
    compare_end: date | None = None,
    session: AsyncSession = Depends(get_db),
) -> InsightsOverview:
    _check_range(start, end)
    if granularity not in insights.GRANULARITIES:
        raise HTTPException(status_code=422, detail="granularity must be day, week or month")
    if (compare_start is None) != (compare_end is None):
        raise HTTPException(status_code=422, detail="compare_start and compare_end go together")
    if compare_start is not None:
        _check_range(compare_start, compare_end, "compare ")
    return await insights.compute_overview(
        session, start, end, granularity, _parse_channels(channels), include_failed_builds, compare_start, compare_end
    )


@router.get("/products", response_model=InsightsProducts)
async def products(
    start: date,
    end: date,
    view: str = "products",
    channels: str | None = None,
    include_failed_builds: bool = True,
    granularity: str = "week",
    q: str | None = None,
    sort: str = "net_profit",
    descending: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    product_id: int | None = None,
    session: AsyncSession = Depends(get_db),
) -> InsightsProducts:
    """Product, variant or category rows, sorted and paged on the server. `product_id` narrows
    the variants view to one product, which is how a product row expands."""
    _check_range(start, end)
    if view not in breakdown.VIEWS:
        raise HTTPException(status_code=422, detail="view must be products, variants or categories")
    if sort not in breakdown.SORT_KEYS:
        raise HTTPException(status_code=422, detail=f"sort must be one of: {', '.join(breakdown.SORT_KEYS)}")
    if granularity not in insights.GRANULARITIES:
        raise HTTPException(status_code=422, detail="granularity must be day, week or month")
    return await breakdown.compute_products(
        session,
        start,
        end,
        _parse_channels(channels),
        view,
        include_failed_builds,
        granularity,
        q,
        sort,
        descending,
        page,
        page_size,
        product_id,
    )


@router.get("/costs", response_model=InsightsCosts)
async def costs(
    start: date,
    end: date,
    channels: str | None = None,
    include_failed_builds: bool = True,
    session: AsyncSession = Depends(get_db),
) -> InsightsCosts:
    _check_range(start, end)
    return await breakdown.compute_costs(session, start, end, _parse_channels(channels), include_failed_builds)


@router.get("/quality", response_model=InsightsQuality)
async def quality(
    start: date,
    end: date,
    channels: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> InsightsQuality:
    _check_range(start, end)
    return await insights.compute_quality(session, start, end, _parse_channels(channels))
