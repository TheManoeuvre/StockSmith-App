import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.deps import require_auth, require_host
from app.schemas.backup import BackupManifestRead
from app.services import backup, backup_scheduler, listing_push, maintenance, restore, sync_scheduler
from app.services.backup_archive import BackupError
from app.services.restore import RestoreError, RestoreVersionError

logger = logging.getLogger("stocksmith.restore")

# require_host on the whole router, not just the destructive endpoints: even reading which
# restore is staged is only meaningful on the machine that can act on it.
router = APIRouter(
    prefix="/restore",
    tags=["restore"],
    dependencies=[Depends(require_auth), Depends(require_host)],
)


class StageByFilename(BaseModel):
    filename: str


class StagedRestoreRead(BaseModel):
    staged: bool
    source_filename: str | None = None
    requested_at: str | None = None
    manifest: BackupManifestRead | None = None


async def _quiesce_background_work() -> None:
    """Stop everything that writes in the background, then enter maintenance.

    Ordering matters: the flag goes up first so no new request can start work, then the
    background writers are stopped. A commit landing after bootstrap takes its pre-restore
    snapshot but before the process dies is data that vanishes if the restore is rolled back.
    """
    maintenance.enter("restore_staged")
    sync_scheduler.stop()
    backup_scheduler.stop()
    await listing_push.quiesce()


def _resume_background_work() -> None:
    maintenance.exit()
    sync_scheduler.start()
    backup_scheduler.start()


def _to_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RestoreVersionError):
        # 409 rather than 400: the request is well-formed, it's the state of this install that
        # makes it impossible. The message tells the user which version they need.
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/pending", response_model=StagedRestoreRead)
async def get_pending_restore() -> StagedRestoreRead:
    marker = restore.read_pending()
    if marker is None or marker.get("state") != "pending":
        return StagedRestoreRead(staged=False)
    return StagedRestoreRead(
        staged=True,
        source_filename=marker.get("source_filename"),
        requested_at=marker.get("requested_at"),
        manifest=BackupManifestRead(**marker["manifest"]) if marker.get("manifest") else None,
    )


@router.delete("/pending", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_pending_restore() -> None:
    """Abandon a staged restore and put the app back to work.

    Allowlisted through the maintenance middleware — it is the escape hatch from the state the
    middleware enforces, so it has to remain reachable while that state is active.
    """
    restore.clear_staging()
    _resume_background_work()


@router.post("/stage", response_model=BackupManifestRead)
async def stage_existing_backup(payload: StageByFilename) -> BackupManifestRead:
    try:
        archive = backup.resolve_archive(payload.filename)
    except BackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    try:
        manifest = restore.stage(archive, source_filename=payload.filename, requested_from="127.0.0.1")
    except RestoreError as exc:
        raise _to_error(exc) from exc

    await _quiesce_background_work()
    return BackupManifestRead(**manifest.to_dict())


@router.post("/stage-upload", response_model=BackupManifestRead)
async def stage_uploaded_backup(file: UploadFile = File(...)) -> BackupManifestRead:
    """Stage an archive the user copied off OneDrive (or anywhere else) by hand.

    Streamed to a temp file rather than read into memory — these carry every product image.
    """
    suffix = Path(file.filename or "backup.zip").name
    tmp_dir = Path(tempfile.mkdtemp(prefix="stocksmith-upload-"))
    tmp_path = tmp_dir / suffix
    try:
        with tmp_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            manifest = restore.stage(tmp_path, source_filename=suffix, requested_from="127.0.0.1")
        except (RestoreError, BackupError) as exc:
            raise _to_error(exc) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    await _quiesce_background_work()
    return BackupManifestRead(**manifest.to_dict())


@router.post("/acknowledge", status_code=status.HTTP_204_NO_CONTENT)
async def acknowledge_restore() -> None:
    restore.acknowledge_last_restore()
