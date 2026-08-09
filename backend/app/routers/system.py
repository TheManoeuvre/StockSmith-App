"""Process- and database-level status, for clients that need to know whether the backend is
usable at all.

Deliberately unauthenticated and mounted *outside* the /api/v1 prefix, alongside /healthz. Both
properties are load-bearing rather than incidental: when a restore puts the backend into
maintenance, every /api/v1 route answers 503, and the client polling to find out when that ends
must not be one of them. Keeping it out of the authenticated surface also means the Tauri shell
can probe it before the frontend has credentials.

Contains no secrets and does no marketplace I/O — the same bar /healthz meets, and the reason
it's safe to leave open. The version and schema revision it exposes are the same facts the
installer and the release notes publish.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.services import maintenance, restore, system_status

router = APIRouter(tags=["system"])


class LastRestoreRead(BaseModel):
    state: str
    completed_at: str | None
    error: str | None
    source_filename: str | None
    prerestore_filename: str | None
    acknowledged: bool


class SystemStatus(BaseModel):
    status: str
    phase: str | None
    app_version: str
    alembic_revision: str | None
    data_fingerprint: str
    last_restore: LastRestoreRead | None


@router.get("/system/status", response_model=SystemStatus)
async def get_system_status(session: AsyncSession = Depends(get_db)) -> SystemStatus:
    phase = maintenance.current_phase()
    last = restore.read_last_restore()
    return SystemStatus(
        status="maintenance" if phase else "ok",
        phase=phase,
        app_version=settings.app_version,
        alembic_revision=await system_status.alembic_revision(session),
        # Pairs the database's lineage id with when it was last restored. The id alone can't
        # catch restoring an older backup of the *same* database, where the lineage is
        # legitimately unchanged but the rows are not.
        data_fingerprint=await system_status.data_fingerprint(
            session, last_restore_completed_at=last.completed_at if last else None
        ),
        last_restore=LastRestoreRead(**last.to_dict()) if last else None,
    )
