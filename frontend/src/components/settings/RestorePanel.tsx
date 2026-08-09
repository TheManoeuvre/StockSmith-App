import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { Backup } from "../../api/backups";
import { backupsApi } from "../../api/backups";
import { restoreApi } from "../../api/restore";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { backendHostname, isHostDevice, restartApp } from "../../lib/tauri";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ErrorBanner } from "../common/ErrorBanner";
import { describeContents, formatWhen } from "./backupFormatting";

export function RestorePanel() {
  const queryClient = useQueryClient();
  const guard = useGuard();

  const [isHost, setIsHost] = useState<boolean | null>(null);
  const [hostname, setHostname] = useState<string | null>(null);
  useEffect(() => {
    isHostDevice().then(setIsHost);
    backendHostname().then(setHostname);
  }, []);

  const { data: backups } = useQuery({ queryKey: ["backups"], queryFn: backupsApi.list });
  const { data: staged } = useQuery({
    queryKey: ["restore", "pending"],
    queryFn: restoreApi.getPending,
    // Only the host can read this meaningfully, and on a thin client it 403s.
    enabled: isHost === true,
  });

  const [selected, setSelected] = useState<Backup | null>(null);

  const stageMutation = useMutation({
    mutationFn: (filename: string) => restoreApi.stage(filename),
    onSuccess: () => {
      setSelected(null);
      queryClient.invalidateQueries({ queryKey: ["restore", "pending"] });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => restoreApi.cancelPending(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore", "pending"] }),
  });

  const restartMutation = useMutation({ mutationFn: () => restartApp() });

  if (isHost === null) return null;

  if (!isHost) {
    return (
      <div className="flex flex-col gap-2 rounded border border-slate-300 p-3">
        <h2 className="font-medium">Restore</h2>
        <p className="text-sm text-slate-500">
          Restoring runs on the computer that hosts StockSmith
          {hostname ? (
            <>
              {" "}
              — this device is connected to <span className="font-medium">{hostname}</span>
            </>
          ) : null}
          . Open StockSmith on that computer to restore a backup.
        </p>
      </div>
    );
  }

  if (staged?.staged) {
    return (
      <div className="flex flex-col gap-3 rounded border border-amber-300 bg-amber-50 p-3">
        <h2 className="font-medium">A restore is waiting to be applied</h2>
        <p className="text-sm text-amber-900">
          StockSmith will restore the backup from {formatWhen(staged.manifest?.created_at)} the next time
          it starts. Everything else is paused until then.
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => restartMutation.mutate()}
            disabled={restartMutation.isPending}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {restartMutation.isPending ? "Restarting…" : "Restart now"}
          </button>
          <button
            onClick={() => cancelMutation.mutate()}
            disabled={cancelMutation.isPending}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Cancel restore
          </button>
        </div>
        <ErrorBanner error={restartMutation.error ?? cancelMutation.error} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <h2 className="font-medium">Restore</h2>
        <p className="text-sm text-slate-500">
          Replaces everything currently in StockSmith with the contents of a backup. A snapshot of your
          current data is taken first, so this can be undone.
        </p>
      </div>

      {!backups || backups.length === 0 ? (
        <p className="text-sm text-slate-500">There are no backups to restore from yet.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {backups.map((item) => (
            <li
              key={`${item.location}:${item.filename}`}
              className="flex items-center justify-between gap-4 border-b border-slate-100 py-1 text-sm"
            >
              <span>
                {formatWhen(item.manifest.created_at)}
                <span className="ml-2 text-slate-400">{describeContents(item.manifest.counts)}</span>
              </span>
              <button onClick={() => setSelected(item)} className="text-red-600 underline">
                Restore this
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorBanner error={stageMutation.error} />

      <ConfirmDialog
        open={selected !== null}
        title="Restore this backup?"
        confirmLabel="Restore and restart"
        // The one place in the app that earns a typed confirmation: it replaces the entire
        // database, and a misclick here is not something the user can spot and undo casually.
        requireTypedText="RESTORE"
        busy={stageMutation.isPending}
        body={
          selected && (
            <div className="flex flex-col gap-2">
              <p>
                This will replace everything in StockSmith with the backup from{" "}
                <span className="font-medium">{formatWhen(selected.manifest.created_at)}</span> (
                {describeContents(selected.manifest.counts)}).
              </p>
              <p className="font-medium text-red-700">
                Everything you have done since then will be permanently replaced.
              </p>
              {!selected.manifest.includes_config && (
                <p>
                  Marketplace connections are not included in backups. You will need to reconnect Etsy
                  and eBay afterwards.
                </p>
              )}
              <p>
                A snapshot of your current data will be saved first, so this can be undone. StockSmith
                will restart to finish.
              </p>
            </div>
          )
        }
        onConfirm={() => {
          if (!selected) return;
          // The restart bypasses the window-close guard by design (it emits ExitRequested, not
          // CloseRequested), so unsaved work elsewhere has to be caught here instead.
          guard.attempt(() => stageMutation.mutate(selected.filename));
        }}
        onCancel={() => setSelected(null)}
      />
    </div>
  );
}
