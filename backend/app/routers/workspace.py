from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.deps import require_auth
from app.services import workspace
from app.services.sample_data import SAMPLE_BUSINESS

router = APIRouter(prefix="/workspace", tags=["workspace"], dependencies=[Depends(require_auth)])


class WorkspaceRead(BaseModel):
    mode: Literal["real", "sample"]
    # False in the dev loop, where nothing at start-up would act on a switch.
    can_switch: bool
    sample_business: str


class WorkspaceSwitch(BaseModel):
    mode: Literal["real", "sample"]


def _read() -> WorkspaceRead:
    return WorkspaceRead(
        mode=workspace.current(),  # type: ignore[arg-type]
        can_switch=workspace.switching_available(),
        sample_business=SAMPLE_BUSINESS,
    )


@router.get("", response_model=WorkspaceRead)
async def get_workspace() -> WorkspaceRead:
    return _read()


@router.put("", response_model=WorkspaceRead)
async def switch_workspace(payload: WorkspaceSwitch) -> WorkspaceRead:
    """Records the switch for the next start. The caller restarts the app to apply it; until
    then this process keeps serving the database it opened."""
    if not workspace.switching_available():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sample data is only available in the installed desktop app.",
        )
    if payload.mode == workspace.current():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Already using the {payload.mode} workspace.")
    workspace.request(payload.mode)
    return _read()
