from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.schemas.backup import (
    BackupManifestRead,
    BackupRead,
    BackupSettingsRead,
    BackupSettingsUpdate,
    SecondaryDirCheck,
)
from app.services import backup
from app.services.backup_archive import BackupError

router = APIRouter(prefix="/backups", tags=["backups"], dependencies=[Depends(require_auth)])


def _to_read(item: backup.BackupFile) -> BackupRead:
    return BackupRead(
        filename=item.filename,
        location=item.location,
        size_bytes=item.size_bytes,
        manifest=BackupManifestRead(**item.manifest.to_dict()),
    )


def _settings_read(row, supported: bool) -> BackupSettingsRead:
    return BackupSettingsRead(
        supported=supported,
        unsupported_reason=None if supported else backup.unsupported_reason(),
        scheduled_enabled=row.scheduled_enabled,
        scheduled_hour_local=row.scheduled_hour_local,
        retention_count=row.retention_count,
        secondary_dir=row.secondary_dir,
        secondary_dir_last_ok_at=row.secondary_dir_last_ok_at,
        secondary_dir_last_error=row.secondary_dir_last_error,
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        last_run_error=row.last_run_error,
    )


@router.get("", response_model=list[BackupRead])
async def list_backups(session: AsyncSession = Depends(get_db)) -> list[BackupRead]:
    if not backup.is_supported():
        return []
    return [_to_read(item) for item in await backup.list_backups(session)]


@router.post("", response_model=BackupRead, status_code=status.HTTP_201_CREATED)
async def create_backup(session: AsyncSession = Depends(get_db)) -> BackupRead:
    if not backup.is_supported():
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=backup.unsupported_reason())

    lock = backup.get_lock()
    if lock.locked():
        # Shared with the scheduler. Reporting a conflict beats queueing: the caller is a person
        # who just clicked a button and should be told, not left watching a spinner.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A backup is already running.")

    async with lock:
        try:
            return _to_read(await backup.run_backup(session, kind="manual"))
        except BackupError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/settings", response_model=BackupSettingsRead)
async def get_backup_settings(session: AsyncSession = Depends(get_db)) -> BackupSettingsRead:
    return _settings_read(await backup.get_settings_row(session), backup.is_supported())


@router.put("/settings", response_model=BackupSettingsRead)
async def update_backup_settings(
    payload: BackupSettingsUpdate, session: AsyncSession = Depends(get_db)
) -> BackupSettingsRead:
    row = await backup.get_settings_row(session)
    row.scheduled_enabled = payload.scheduled_enabled
    row.scheduled_hour_local = payload.scheduled_hour_local
    row.retention_count = payload.retention_count

    secondary = (payload.secondary_dir or "").strip() or None
    if secondary != row.secondary_dir:
        # Validate on change only, so saving an unrelated field doesn't fail because a removable
        # drive happens to be unplugged right now.
        if secondary is not None:
            try:
                backup.validate_secondary_dir(secondary)
            except BackupError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
            row.secondary_dir_last_ok_at = datetime.now(timezone.utc)
        row.secondary_dir = secondary
        row.secondary_dir_last_error = None

    await session.commit()
    await session.refresh(row)
    return _settings_read(row, backup.is_supported())


@router.post("/settings/validate-secondary", status_code=status.HTTP_204_NO_CONTENT)
async def check_secondary_dir(payload: SecondaryDirCheck) -> None:
    try:
        backup.validate_secondary_dir(payload.path)
    except BackupError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{filename}/download")
async def download_backup(filename: str) -> FileResponse:
    try:
        path = backup.resolve_archive(filename)
    except BackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(path, filename=filename, media_type="application/zip")


@router.delete("/{filename}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(filename: str) -> None:
    try:
        backup.delete_backup(filename)
    except BackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
