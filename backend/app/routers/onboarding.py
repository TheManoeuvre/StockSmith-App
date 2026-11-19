from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.services import onboarding
from app.services.general_settings import get_general_settings

router = APIRouter(prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(require_auth)])


class SetupStepRead(BaseModel):
    key: str
    done: bool
    optional: bool


class SetupStatusRead(BaseModel):
    steps: list[SetupStepRead]
    dismissed: bool
    # Every step done, optional ones included — the checklist has nothing left to say.
    complete: bool


class ChecklistUpdate(BaseModel):
    dismissed: bool


async def _status(session: AsyncSession) -> SetupStatusRead:
    steps = await onboarding.setup_steps(session)
    settings = await get_general_settings(session)
    return SetupStatusRead(
        steps=[SetupStepRead(key=s.key, done=s.done, optional=s.optional) for s in steps],
        dismissed=settings.setup_checklist_dismissed,
        complete=all(s.done for s in steps),
    )


@router.get("/status", response_model=SetupStatusRead)
async def get_setup_status(session: AsyncSession = Depends(get_db)) -> SetupStatusRead:
    return await _status(session)


@router.put("/checklist", response_model=SetupStatusRead)
async def update_checklist(payload: ChecklistUpdate, session: AsyncSession = Depends(get_db)) -> SetupStatusRead:
    await onboarding.set_dismissed(session, payload.dismissed)
    return await _status(session)
