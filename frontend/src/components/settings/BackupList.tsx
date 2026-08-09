import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { backupsApi, type Backup } from "../../api/backups";
import { backupDownloadUrl } from "../../api/client";
import { saveFileTo } from "../../lib/tauri";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";
import { describeContents, formatBytes, formatWhen } from "./backupFormatting";

const LOCATION_LABELS: Record<Backup["location"], string> = {
  primary: "This computer",
  secondary: "Second folder",
  "pre-restore": "Before a restore",
};

const KIND_LABELS: Record<Backup["manifest"]["kind"], string> = {
  manual: "Manual",
  scheduled: "Scheduled",
  "pre-restore": "Automatic",
};

export function BackupList() {
  const queryClient = useQueryClient();
  const { data: backups, isLoading } = useQuery({ queryKey: ["backups"], queryFn: backupsApi.list });

  const [pendingDelete, setPendingDelete] = useState<Backup | null>(null);
  const [savedTo, setSavedTo] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<Error | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (filename: string) => backupsApi.remove(filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["backups"] });
      setPendingDelete(null);
    },
  });

  const downloadMutation = useMutation({
    mutationFn: async (filename: string) => {
      setDownloadError(null);
      const { url, headers } = await backupDownloadUrl(filename);
      return saveFileTo(url, headers, filename);
    },
    onSuccess: (path) => setSavedTo(path),
    onError: (error: Error) => setDownloadError(error),
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading backups…</p>;

  if (!backups || backups.length === 0) {
    return <p className="text-sm text-slate-500">No backups yet.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Taken</th>
              <th className="p-2">Contents</th>
              <th className="p-2">Size</th>
              <th className="p-2">Where</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {backups.map((item) => (
              <tr key={`${item.location}:${item.filename}`} className="border-b border-slate-100 align-top">
                <td className="p-2">
                  <div>{formatWhen(item.manifest.created_at)}</div>
                  <div className="text-xs text-slate-400">
                    {KIND_LABELS[item.manifest.kind]} · v{item.manifest.app_version}
                  </div>
                </td>
                <td className="p-2">
                  <div>{describeContents(item.manifest.counts)}</div>
                  <div className="text-xs text-slate-400">
                    {item.manifest.asset_file_count} asset files
                    {item.manifest.skipped_assets.length > 0 &&
                      ` · ${item.manifest.skipped_assets.length} skipped`}
                  </div>
                </td>
                <td className="p-2 whitespace-nowrap tabular-nums">{formatBytes(item.size_bytes)}</td>
                <td className="p-2 text-slate-500">{LOCATION_LABELS[item.location]}</td>
                <td className="p-2">
                  <div className="flex gap-2">
                    <button
                      onClick={() => downloadMutation.mutate(item.filename)}
                      disabled={downloadMutation.isPending}
                      className="text-slate-600 underline disabled:opacity-50"
                    >
                      Save a copy
                    </button>
                    <button onClick={() => setPendingDelete(item)} className="text-red-600">
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {savedTo && <p className="text-sm text-green-700">Saved to {savedTo}.</p>}
      <ErrorBanner error={downloadError ?? deleteMutation.error} />

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete this backup?"
        confirmLabel="Delete backup"
        busy={deleteMutation.isPending}
        // No typed confirmation: this deletes one archive, not your data. Reserve that friction
        // for restore, or it just trains people to type without reading.
        body={
          pendingDelete && (
            <p>
              The backup taken on{" "}
              <span className="font-medium">{formatWhen(pendingDelete.manifest.created_at)}</span> will be
              deleted from this computer. Copies in your second folder are not affected.
            </p>
          )
        }
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete.filename)}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
