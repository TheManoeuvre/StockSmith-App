import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { platformsApi, type PlatformSyncSummary } from "../api/platforms";

// The root layout already polls the summary every minute for the sidebar indicator; this
// shares that query, so at rest it adds no requests. While a run is in flight the poll
// tightens so the page refreshes within seconds of the import landing rather than up to a
// minute later — a run's finish is the one moment this hook exists to catch.
const IDLE_POLL_MS = 60_000;
const RUNNING_POLL_MS = 5_000;

// One run is identified by when it started plus its state: the same run flips
// running→success without changing last_sync_at, and a run that starts and finishes
// between two polls changes last_sync_at without ever being seen as running.
function signature(s: PlatformSyncSummary): string {
  return `${s.last_sync_at ?? ""}|${s.last_sync_status ?? ""}`;
}

/**
 * Refresh the order queries whenever a marketplace sync run finishes, so an orders page
 * left open picks up what the background sync just imported. Manual "Sync now" invalidates
 * on its own; this covers the scheduled runs nothing on the page ever hears about.
 */
export function useRefreshOnSync(): void {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["platforms", "sync-summary"],
    queryFn: () => platformsApi.syncSummary(),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.last_sync_status === "running") ? RUNNING_POLL_MS : IDLE_POLL_MS,
    retry: false,
  });

  // Null until the first summary arrives: what was already there on mount is not a run
  // that finished while we were watching, and the queries are fresh at that point anyway.
  const seen = useRef<Map<string, string> | null>(null);

  useEffect(() => {
    if (!data) return;
    const previous = seen.current;
    seen.current = new Map(data.map((s) => [s.platform, signature(s)]));
    if (!previous) return;

    // Errors count too: a run can import some orders before it fails on a later one.
    const finished = data.some(
      (s) => s.last_sync_status !== "running" && previous.get(s.platform) !== signature(s),
    );
    if (!finished) return;
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    queryClient.invalidateQueries({ queryKey: ["order-counts"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  }, [data, queryClient]);
}
