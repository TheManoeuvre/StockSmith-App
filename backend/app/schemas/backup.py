from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BackupManifestRead(BaseModel):
    format_version: int
    app_version: str
    alembic_revision: str | None
    created_at: str
    kind: str
    db_bytes: int
    asset_file_count: int
    asset_bytes: int
    counts: dict[str, int]
    includes_config: bool
    skipped_assets: list[str]


class BackupRead(BaseModel):
    filename: str
    location: str
    size_bytes: int
    manifest: BackupManifestRead


class BackupSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # False on a Postgres backend, where the whole feature is unavailable. The UI reads this to
    # render an explanation rather than controls that would 501.
    supported: bool
    unsupported_reason: str | None = None

    scheduled_enabled: bool
    scheduled_hour_local: int
    retention_count: int
    secondary_dir: str | None
    secondary_dir_last_ok_at: datetime | None
    secondary_dir_last_error: str | None
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_error: str | None


class BackupSettingsUpdate(BaseModel):
    scheduled_enabled: bool
    scheduled_hour_local: int = Field(ge=0, le=23)
    retention_count: int = Field(ge=1, le=100)
    # Empty string clears it — a text field the user has blanked out means "stop copying there",
    # which is different from "leave unchanged" and needs to be expressible.
    secondary_dir: str | None = None


class SecondaryDirCheck(BaseModel):
    path: str
