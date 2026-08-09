import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { backupsApi, type BackupSettings as BackupSettingsValue } from "../../api/backups";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { pickDirectory } from "../../lib/tauri";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { BackupList } from "./BackupList";
import { RestoreOutcomeBanner } from "./RestoreOutcomeBanner";
import { RestorePanel } from "./RestorePanel";

interface ScheduleForm {
  scheduledEnabled: boolean;
  scheduledHour: number;
  retentionCount: string;
  secondaryDir: string;
}

const EMPTY_FORM: ScheduleForm = {
  scheduledEnabled: true,
  scheduledHour: 3,
  retentionCount: "7",
  secondaryDir: "",
};

function formatWhen(iso: string | null): string {
  if (!iso) return "never";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function BackupSettings() {
  const queryClient = useQueryClient();
  const {
    data: settings,
    isLoading: settingsLoading,
    error: settingsError,
  } = useQuery({ queryKey: ["backups", "settings"], queryFn: backupsApi.getSettings });

  const seed = useMemo<ScheduleForm | undefined>(
    () =>
      settings
        ? {
            scheduledEnabled: settings.scheduled_enabled,
            scheduledHour: settings.scheduled_hour_local,
            retentionCount: String(settings.retention_count),
            secondaryDir: settings.secondary_dir ?? "",
          }
        : undefined,
    [settings]
  );

  // Buffered: it holds a free-text path and a number, and the backend validates the folder on
  // save — none of which survives contact with save-per-keystroke.
  const {
    value: form,
    setValue: setForm,
    isDirty,
    markSaved,
  } = useEditableCopy<ScheduleForm>({
    key: "backup/settings",
    label: "Backup settings",
    initial: EMPTY_FORM,
    seed,
    seedKey: "settings",
  });

  const setField = <K extends keyof ScheduleForm>(field: K, next: ScheduleForm[K]) =>
    setForm((prev) => ({ ...prev, [field]: next }));

  const saveMutation = useMutation({
    mutationFn: () =>
      backupsApi.updateSettings({
        scheduled_enabled: form.scheduledEnabled,
        scheduled_hour_local: form.scheduledHour,
        retention_count: Number(form.retentionCount) || 1,
        secondary_dir: form.secondaryDir.trim() || null,
      }),
    onSuccess: (saved: BackupSettingsValue) => {
      markSaved({
        scheduledEnabled: saved.scheduled_enabled,
        scheduledHour: saved.scheduled_hour_local,
        retentionCount: String(saved.retention_count),
        secondaryDir: saved.secondary_dir ?? "",
      });
      queryClient.invalidateQueries({ queryKey: ["backups", "settings"] });
    },
  });
  const saveStatus = useSaveStatus(saveMutation.status);

  const [runError, setRunError] = useState<Error | null>(null);
  const runMutation = useMutation({
    mutationFn: () => {
      setRunError(null);
      return backupsApi.create();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["backups"] });
      queryClient.invalidateQueries({ queryKey: ["backups", "settings"] });
    },
    onError: (error: Error) => setRunError(error),
  });

  const browseForFolder = async () => {
    try {
      const chosen = await pickDirectory();
      if (chosen) setField("secondaryDir", chosen);
    } catch {
      // Browser preview has no native picker — the path can still be typed by hand.
    }
  };

  if (settingsLoading) return <p className="text-sm text-slate-500">Loading…</p>;

  if (!settings) {
    // Distinguished from loading deliberately: a failed query used to leave this panel sitting
    // on "Loading…" for good, which reads as a hung app rather than an unreachable backend.
    // The plain fallback line covers the case where the query has given up without surfacing an
    // Error object — saying nothing at all is the one outcome that isn't allowed here.
    return (
      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <h2 className="font-medium">Backups</h2>
        {settingsError ? (
          <ErrorBanner error={settingsError} />
        ) : (
          <p className="text-sm text-slate-500">
            Couldn't load backup settings — check the connection to the backend.
          </p>
        )}
      </div>
    );
  }

  if (!settings.supported) {
    return (
      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <h2 className="font-medium">Backup &amp; restore</h2>
        <p className="text-sm text-slate-500">{settings.unsupported_reason}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <RestoreOutcomeBanner />

      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <div>
          <h2 className="font-medium">Backups</h2>
          <p className="text-sm text-slate-500">
            A backup is a single zip holding your database and every product image. Marketplace
            connections are deliberately left out, so restoring onto a different computer means
            reconnecting Etsy and eBay — nothing else is lost.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => runMutation.mutate()}
            disabled={runMutation.isPending}
            className="w-fit rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {runMutation.isPending ? "Backing up…" : "Back up now"}
          </button>
          <span className="text-sm text-slate-500">
            Last backup: {formatWhen(settings.last_run_at)}
            {settings.last_run_status === "error" && " — failed"}
          </span>
        </div>
        {settings.last_run_error && (
          <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">
            Last attempt failed: {settings.last_run_error}
          </p>
        )}
        <ErrorBanner error={runError} />
      </div>

      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <h2 className="font-medium">Schedule</h2>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.scheduledEnabled}
            onChange={(e) => setField("scheduledEnabled", e.target.checked)}
          />
          Back up automatically every day
        </label>

        <div className="grid grid-cols-2 gap-3 sm:max-w-md">
          <label className="flex flex-col gap-1 text-sm">
            Time of day
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={form.scheduledHour}
              disabled={!form.scheduledEnabled}
              onChange={(e) => setField("scheduledHour", Number(e.target.value))}
            >
              {Array.from({ length: 24 }, (_, hour) => (
                <option key={hour} value={hour}>
                  {String(hour).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Backups to keep
            <input
              type="number"
              min="1"
              max="100"
              className="rounded border border-slate-300 px-2 py-1"
              value={form.retentionCount}
              onChange={(e) => setField("retentionCount", e.target.value)}
            />
          </label>
        </div>
        <p className="text-xs text-slate-500">
          Runs while StockSmith is open. If the computer was asleep at the scheduled time, the backup
          runs shortly after it wakes rather than being skipped.
        </p>

        <label className="flex flex-col gap-1 text-sm">
          Also copy each backup to
          <div className="flex gap-2">
            <input
              className="flex-1 rounded border border-slate-300 px-2 py-1"
              placeholder="e.g. C:\Users\you\OneDrive\StockSmith"
              value={form.secondaryDir}
              onChange={(e) => setField("secondaryDir", e.target.value)}
            />
            <button type="button" onClick={browseForFolder} className="rounded border border-slate-300 px-3 text-sm">
              Browse…
            </button>
          </div>
        </label>
        <p className="text-xs text-slate-500">
          Point this at a OneDrive or Dropbox folder and your backups end up somewhere other than
          this machine — which is the copy that still helps if this computer is the thing that fails.
          Leave blank to keep backups here only.
        </p>
        {settings.secondary_dir_last_error && (
          <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">
            Could not copy to that folder last time: {settings.secondary_dir_last_error}
          </p>
        )}
        {settings.secondary_dir && !settings.secondary_dir_last_error && settings.secondary_dir_last_ok_at && (
          <p className="text-xs text-slate-500">
            Last copied there {formatWhen(settings.secondary_dir_last_ok_at)}.
          </p>
        )}

        <div className="flex items-center gap-2">
          <SaveButton
            isDirty={isDirty}
            isPending={saveMutation.isPending}
            status={saveStatus}
            onClick={() => saveMutation.mutate()}
          >
            Save
          </SaveButton>
        </div>
        <ErrorBanner error={saveMutation.error} />
      </div>

      <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
        <h2 className="font-medium">Available backups</h2>
        <BackupList />
      </div>

      <RestorePanel />
    </div>
  );
}
