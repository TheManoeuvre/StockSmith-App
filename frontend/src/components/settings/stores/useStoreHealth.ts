import { useQuery } from "@tanstack/react-query";
import { platformsApi, type PlatformStatus, type PlatformSyncSummary } from "../../../api/platforms";
import type { ListingPlatform } from "../../../api/types";

// Status is per-platform and cheap; the summary is cross-platform and is what the sidebar's
// "Sync problem" indicator reads. Both hub cards and store pages read both, so the page can
// never say "all green" while the sidebar says otherwise — that split is exactly what the
// old page did, by inspecting only the last ten push-log rows.
const STATUS_POLL_MS = 30_000;
const SUMMARY_POLL_MS = 60_000;

export type StoreState = "not-connected" | "reconnect" | "sync-failed" | "pushes-failing" | "syncing" | "ok";

export interface StoreHealth {
  status: PlatformStatus | undefined;
  summary: PlatformSyncSummary | undefined;
  connected: boolean;
  /** The one thing the card's chip should say. Worst problem wins. */
  state: StoreState;
  failingPushCount: number;
}

export function useStoreHealth(platform: ListingPlatform): StoreHealth {
  const { data: status } = useQuery({
    queryKey: ["platforms", platform, "status"],
    queryFn: () => platformsApi.status(platform),
    refetchInterval: STATUS_POLL_MS,
  });
  const { data: summaries } = useQuery({
    queryKey: ["platforms", "sync-summary"],
    queryFn: () => platformsApi.syncSummary(),
    refetchInterval: SUMMARY_POLL_MS,
    retry: false,
  });
  const summary = summaries?.find((s) => s.platform === platform);
  const connected = status?.connected ?? false;
  const failingPushCount = summary?.failing_push_count ?? 0;

  const state: StoreState = !connected
    ? "not-connected"
    : status?.needs_reconnect
      ? "reconnect"
      : status?.last_sync_status === "error"
        ? "sync-failed"
        : failingPushCount > 0
          ? "pushes-failing"
          : status?.last_sync_status === "running"
            ? "syncing"
            : "ok";

  return { status, summary, connected, state, failingPushCount };
}

/** "4m ago" / "3h ago" / "2d ago", the absolute date past a week. */
export function formatRelative(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

/** Chip label + colour for a store's state. Shared by the hub card and the store page header. */
export function stateChip(state: StoreState, failingPushCount: number): { label: string; className: string } {
  switch (state) {
    case "not-connected":
      return {
        label: "Not connected",
        className: "bg-slate-100 text-slate-600",
      };
    case "reconnect":
      return {
        label: "Reconnect needed",
        className: "bg-amber-100 text-amber-800",
      };
    case "sync-failed":
      return { label: "Sync failed", className: "bg-red-100 text-red-800" };
    case "pushes-failing":
      return {
        label: `${failingPushCount} ${failingPushCount === 1 ? "listing" : "listings"} not updating`,
        className: "bg-red-100 text-red-800",
      };
    case "syncing":
      return { label: "Syncing…", className: "bg-blue-50 text-blue-700" };
    case "ok":
      return { label: "Connected", className: "bg-green-100 text-green-800" };
  }
}
