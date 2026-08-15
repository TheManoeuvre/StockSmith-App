"""Stock takes.

Phase A carries only the overdue list — the ABC cadence work that has to exist before a
take can be scoped to "whatever is due". The lifecycle routes (create, count, approve,
resolve, CSV) land here in Phase B.

Route ordering matters in this file from the moment `/{stock_take_id}` is added: FastAPI
matches in declaration order, so every literal segment must be declared above it or it
will be swallowed as an id. routers/materials.py has the same hazard and solves it the
same way.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.schemas.abc import DueForCountItemRead
from app.services import abc

router = APIRouter(prefix="/stock-takes", tags=["stock-takes"], dependencies=[Depends(require_auth)])


@router.get("/overdue", response_model=list[DueForCountItemRead])
async def list_overdue(session: AsyncSession = Depends(get_db)) -> list:
    """Everything whose cadence says it wants counting, most overdue first.

    Never-counted items lead the list with a null days_overdue rather than a fabricated
    one — on a database that has never had a stock take, that is every item, which is the
    honest starting state rather than a bug.
    """
    return await abc.compute_due_for_count(session)
