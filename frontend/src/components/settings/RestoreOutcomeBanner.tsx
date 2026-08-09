import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchSystemStatus } from "../../api/client";
import { restoreApi, type SystemStatus } from "../../api/restore";

function formatWhen(iso: string | null): string {
  if (!iso) return "recently";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(undefined, { dateStyle: "long" });
}

/**
 * What happened to the last restore, shown once until dismissed.
 *
 * Exists because the restore finishes during a restart, so there is no request in flight to
 * report back to — the outcome has to be picked up on the next launch instead. Both outcomes
 * need saying: a success comes with consequences the user must act on (reconnecting
 * marketplaces), and a failure would otherwise be completely invisible, since a rolled-back
 * restore leaves the app looking exactly as it did before.
 */
export function RestoreOutcomeBanner() {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ["system", "status"],
    queryFn: () => fetchSystemStatus<SystemStatus>(),
  });

  const acknowledgeMutation = useMutation({
    mutationFn: () => restoreApi.acknowledge(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["system", "status"] }),
  });

  const last = status?.last_restore;
  if (!last || last.acknowledged) return null;

  const failed = last.state === "failed";

  return (
    <div
      className={`flex flex-col gap-2 rounded border p-3 ${
        failed ? "border-red-300 bg-red-50" : "border-green-300 bg-green-50"
      }`}
    >
      <h2 className="font-medium">{failed ? "The last restore did not complete" : "Backup restored"}</h2>
      {failed ? (
        <p className="text-sm text-red-900">{last.error}</p>
      ) : (
        <div className="flex flex-col gap-1 text-sm text-green-900">
          <p>
            Your data was restored from the backup taken {formatWhen(last.completed_at)}.
          </p>
          <p>
            Marketplace connections aren't included in backups — reconnect Etsy and eBay under
            Integrations if you haven't already.
          </p>
          {last.prerestore_filename && (
            <p>
              A snapshot of your previous data was saved as{" "}
              <span className="font-mono text-xs">{last.prerestore_filename}</span>, so this can be
              undone.
            </p>
          )}
        </div>
      )}
      <button
        onClick={() => acknowledgeMutation.mutate()}
        className="self-start rounded border border-slate-300 bg-white px-3 py-1 text-sm"
      >
        Dismiss
      </button>
    </div>
  );
}
