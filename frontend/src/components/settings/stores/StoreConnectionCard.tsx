import { useMutation, useQueryClient } from "@tanstack/react-query";
import { platformsApi } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";
import { openExternalUrl } from "../../../lib/tauri";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../../../lib/platforms";
import { useShopIconUrl } from "../../../hooks/useShopIconUrl";
import { ErrorBanner } from "../../common/ErrorBanner";
import { formatRelative, stateChip, useStoreHealth } from "./useStoreHealth";

/**
 * One store on the Stores & sync hub: the same five facts for every store, in the same
 * places, so Etsy and eBay read as two of one thing. Everything deeper lives on the store
 * page behind "Configure".
 */
export function StoreConnectionCard({ platform, onOpen }: { platform: ListingPlatform; onOpen: () => void }) {
  const label = PLATFORM_LABELS[platform];
  const queryClient = useQueryClient();
  const { status, summary, connected, state, failingPushCount } = useStoreHealth(platform);
  const chip = stateChip(state, failingPushCount);
  const iconUrl = useShopIconUrl(platform, status?.has_shop_icon ?? false, status?.connected_at ?? null);

  const invalidateStatus = () => {
    queryClient.invalidateQueries({
      queryKey: ["platforms", platform, "status"],
    });
    queryClient.invalidateQueries({ queryKey: ["platforms", "sync-summary"] });
  };

  const connectMutation = useMutation({
    mutationFn: async () => {
      const { authorize_url } = await platformsApi.connect(platform, "production");
      await openExternalUrl(authorize_url);
    },
  });
  const disconnectMutation = useMutation({
    mutationFn: () => platformsApi.disconnect(platform),
    onSuccess: invalidateStatus,
  });
  const syncMutation = useMutation({
    mutationFn: () => platformsApi.syncOrders(platform),
    onSuccess: () => {
      invalidateStatus();
      queryClient.invalidateQueries({
        queryKey: ["platforms", platform, "sync-log"],
      });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["order-counts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  const lastSync = status?.last_sync_attempt_at
    ? `${status.last_sync_status === "running" ? "syncing since" : "synced"} ${formatRelative(status.last_sync_attempt_at)}`
    : connected
      ? "never synced"
      : "";

  // One sentence: what's wrong if something is, otherwise what's running.
  const detail = !connected
    ? `Connect to import ${label} orders and keep listed quantities in step with stock.`
    : state === "reconnect"
      ? (status?.needs_reconnect_reason ??
        `Reconnect ${label} to grant the permissions StockSmith now needs.`)
      : state === "sync-failed"
        ? `Last order sync failed${status?.last_sync_error ? `: ${status.last_sync_error}` : "."}`
        : state === "pushes-failing"
          ? `The latest quantity push to ${failingPushCount === 1 ? "one listing" : `${failingPushCount} listings`} was rejected — stock shown on ${label} may be stale.`
          : [
              status?.shop_name ?? (status?.account_id ? `Account ${status.account_id}` : null),
              status?.auto_sync_enabled
                ? `auto-sync every ${status.sync_interval_minutes} min`
                : "manual sync only",
              summary && summary.api_call_budget > 0
                ? `${summary.api_calls_today.toLocaleString()} / ${summary.api_call_budget.toLocaleString()} calls today`
                : null,
            ]
              .filter(Boolean)
              .join(" · ");

  const primary = !connected
    ? {
        label: connectMutation.isPending ? "Opening…" : "Connect",
        run: () => connectMutation.mutate(),
        pending: connectMutation.isPending,
      }
    : state === "reconnect"
      ? {
          label: connectMutation.isPending ? "Opening…" : "Reconnect",
          run: () => connectMutation.mutate(),
          pending: connectMutation.isPending,
        }
      : {
          label: syncMutation.isPending ? "Syncing…" : "Sync now",
          run: () => syncMutation.mutate(),
          pending: syncMutation.isPending,
        };

  return (
    <section
      aria-label={label}
      className={`flex flex-col rounded-[9px] border border-slate-200 border-t-[3px] bg-white p-3.5 ${PLATFORM_COLORS[platform].accent}`}
      style={{ boxShadow: "0 1px 2px rgba(15,23,42,.04)" }}
    >
      <div className="flex items-center gap-2">
        {connected && iconUrl && <img src={iconUrl} alt="" className="h-6 w-6 rounded-full object-cover" />}
        <h2 className="text-sm font-semibold">{label}</h2>
        <span className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold ${chip.className}`}>
          {chip.label}
        </span>
        {status?.environment === "sandbox" && connected && (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800">
            Sandbox
          </span>
        )}
        <span className="flex-1" />
        <span className="text-[11px] text-slate-400">{lastSync}</span>
      </div>
      <p
        className={`mt-2 min-h-[2lh] text-[12.5px] leading-relaxed ${state === "ok" || state === "syncing" || state === "not-connected" ? "text-slate-600" : state === "reconnect" ? "text-amber-800" : "text-red-800"}`}
      >
        {detail}
      </p>
      <ErrorBanner error={connectMutation.error ?? disconnectMutation.error ?? syncMutation.error} />
      <div className="mt-3 flex items-center gap-1.5">
        <button
          type="button"
          onClick={primary.run}
          disabled={primary.pending}
          className="h-7 rounded-md bg-slate-900 px-2.5 text-xs font-semibold text-white disabled:opacity-50"
        >
          {primary.label}
        </button>
        <button
          type="button"
          onClick={onOpen}
          className="h-7 rounded-md border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-900 hover:bg-slate-50"
        >
          Configure
        </button>
        <span className="flex-1" />
        {connected && (
          <button
            type="button"
            onClick={() => disconnectMutation.mutate()}
            disabled={disconnectMutation.isPending}
            className="h-7 rounded-md px-2.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            Disconnect
          </button>
        )}
      </div>
    </section>
  );
}
