import { api } from "./client";

export interface BackupManifest {
  format_version: number;
  app_version: string;
  alembic_revision: string | null;
  created_at: string;
  kind: "manual" | "scheduled" | "pre-restore";
  db_bytes: number;
  asset_file_count: number;
  asset_bytes: number;
  counts: Record<string, number>;
  /** Whether config.json travelled with the archive — drives the "reconnect Etsy/eBay" warning. */
  includes_config: boolean;
  skipped_assets: string[];
}

export interface Backup {
  filename: string;
  location: "primary" | "secondary" | "pre-restore";
  size_bytes: number;
  manifest: BackupManifest;
}

export interface BackupSettings {
  /** False on a Postgres backend, where the feature doesn't apply. */
  supported: boolean;
  unsupported_reason: string | null;
  scheduled_enabled: boolean;
  scheduled_hour_local: number;
  retention_count: number;
  secondary_dir: string | null;
  secondary_dir_last_ok_at: string | null;
  secondary_dir_last_error: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  last_run_error: string | null;
}

export interface BackupSettingsUpdate {
  scheduled_enabled: boolean;
  scheduled_hour_local: number;
  retention_count: number;
  secondary_dir: string | null;
}

export const backupsApi = {
  list: () => api.get<Backup[]>("/backups"),
  create: () => api.post<Backup>("/backups"),
  remove: (filename: string) => api.delete<void>(`/backups/${encodeURIComponent(filename)}`),
  getSettings: () => api.get<BackupSettings>("/backups/settings"),
  updateSettings: (settings: BackupSettingsUpdate) =>
    api.put<BackupSettings>("/backups/settings", settings),
  validateSecondaryDir: (path: string) => api.post<void>("/backups/settings/validate-secondary", { path }),
  downloadPath: (filename: string) => `/backups/${encodeURIComponent(filename)}/download`,
};
