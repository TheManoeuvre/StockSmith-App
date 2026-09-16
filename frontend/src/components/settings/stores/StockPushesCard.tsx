import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi, type BulkListingSyncResult, type PlatformSyncSummary } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { PLATFORM_LABELS } from "../../../lib/platforms";
import { ErrorBanner } from "../../common/ErrorBanner";
import { SettingsCard } from "../SettingsCard";

const RECENT_PUSH_CHECK_SIZE = 25;

/**
 * Outbound quantity pushes for one store: whether they're landing, how much of today's API
 * budget they've used, and a way to re-check every SKU against the live catalogue.
 *
 * The headline count comes from the sync summary — the same figure the sidebar's "Sync
 * problem" indicator reads — so this card can't say "fine" while the sidebar says otherwise.
 * The push log only supplies the detail underneath it.
 */
export function StockPushesCard({
  platform,
  summary,
  failingPushCount,
}: {
  platform: ListingPlatform;
  summary: PlatformSyncSummary | undefined;
  failingPushCount: number;
}) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const [bulkResult, setBulkResult] = useState<BulkListingSyncResult | null>(null);

  const { data: pushLog } = useQuery({
    queryKey: ["platforms", platform, "listing-push-log"],
    queryFn: () => platformsApi.listingPushLog(platform, RECENT_PUSH_CHECK_SIZE, 0),
    refetchInterval: 30_000,
  });
  const recentFailures = pushLog?.items.filter((p) => p.status === "error") ?? [];

  const checkAllMutation = useMutation({
    mutationFn: () => platformsApi.checkAllListings(platform),
    onSuccess: (result) => {
      setBulkResult(result);
      queryClient.invalidateQueries({
        queryKey: ["platforms", platform, "all-sync-status"],
      });
      queryClient.invalidateQueries({
        queryKey: ["platforms", platform, "products"],
      });
    },
  });

  const budget = summary?.api_call_budget ?? 0;
  const calls = summary?.api_calls_today ?? 0;
  const nearBudget = budget > 0 && calls >= budget * 0.8;

  return (
    <SettingsCard
      title="Stock pushes"
      help={`Sellable quantity is sent to ${label} after every stock change.`}
      action={
        <button
          type="button"
          onClick={() => checkAllMutation.mutate()}
          disabled={checkAllMutation.isPending}
          className="h-7 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium disabled:opacity-50"
        >
          {checkAllMutation.isPending ? "Checking…" : "Check every SKU"}
        </button>
      }
    >
      {failingPushCount > 0 ? (
        <div className="rounded border border-red-200 bg-red-50 p-2.5 text-sm text-red-800">
          <p className="font-medium">
            {failingPushCount === 1 ? "One listing isn't" : `${failingPushCount} listings aren't`} receiving
            stock updates — the quantity shown on {label} may be stale.
          </p>
          {recentFailures.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {recentFailures.slice(0, 5).map((p) => (
                <li key={p.id}>
                  {p.product_name ?? `Product #${p.product_id}`}
                  {p.variant_name ? ` — ${p.variant_name}` : ""}: {p.error_message ?? "unknown error"}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-xs">
            The next stock change retries automatically. Under Tools, Unlinked listings finds any that can't
            be reached at all.
          </p>
        </div>
      ) : (
        <p className="text-sm text-slate-600">Recent pushes all landed.</p>
      )}

      {budget > 0 && (
        <p className="text-xs text-slate-500">
          <span className="tabular-nums">
            {calls.toLocaleString()} / {budget.toLocaleString()}
          </span>{" "}
          {label} API calls today
          {nearBudget && (
            <span className="text-amber-700">
              {" "}
              — automatic pushes are paused until usage falls; order sync is unaffected
            </span>
          )}
        </p>
      )}

      <ErrorBanner error={checkAllMutation.error} />
      {bulkResult && (
        <div className="rounded bg-slate-50 p-2 text-sm">
          <strong>{bulkResult.synced_count}</strong> synced, <strong>{bulkResult.partial_count}</strong>{" "}
          partial, <strong>{bulkResult.not_found_count}</strong> not found on {label} (
          {bulkResult.summaries.length} checked).
        </div>
      )}
    </SettingsCard>
  );
}
