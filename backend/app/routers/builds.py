from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.models.build import Build
from app.schemas.build import BuildCreate, BuildRead
from app.services.builds import create_build

router = APIRouter(prefix="/builds", tags=["builds"], dependencies=[Depends(require_auth)])


@router.post("", response_model=BuildRead, status_code=status.HTTP_201_CREATED)
async def record_build(payload: BuildCreate, session: AsyncSession = Depends(get_db)) -> Build:
    return await create_build(session, payload.product_id, payload.variant_id, payload.qty_built, payload.notes)
