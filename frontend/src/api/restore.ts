import { api } from "./client";
import type { BackupManifest } from "./backups";

export interface StagedRestore {
  staged: boolean;
  source_filename: string | null;
  requested_at: string | null;
  manifest: BackupManifest | null;
}

export interface LastRestore {
  state: "done" | "failed";
  completed_at: string | null;
  error: string | null;
  source_filename: string | null;
  prerestore_filename: string | null;
  acknowledged: boolean;
}

export interface SystemStatus {
  status: "ok" | "maintenance";
  phase: "restore_staged" | "restoring" | null;
  app_version: string;
  alembic_revision: string | null;
  /** Changes when the database underneath has been swapped — see MaintenanceOverlay. */
  data_fingerprint: string;
  last_restore: LastRestore | null;
}

export const restoreApi = {
  getPending: () => api.get<StagedRestore>("/restore/pending"),
  cancelPending: () => api.delete<void>("/restore/pending"),
  stage: (filename: string) => api.post<BackupManifest>("/restore/stage", { filename }),
  acknowledge: () => api.post<void>("/restore/acknowledge"),
};
