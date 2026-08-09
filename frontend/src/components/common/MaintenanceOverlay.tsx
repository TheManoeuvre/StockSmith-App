import { useQueryClient } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { fetchSystemStatus } from "../../api/client";
import type { SystemStatus } from "../../api/restore";
import { getSettings } from "../../lib/tauri";

const POLL_INTERVAL_MS = 3000;

/**
 * Covers the app while the host is restoring a backup, and cleans up afterwards.
 *
 * Polls /system/status rather than any /api/v1 route, because during a restore everything under
 * that prefix answers 503 — including, if this were wired to it, the very request that would
 * tell us the outage had ended.
 *
 * The cleanup is the part that actually matters. When the backend comes back, its
 * `data_fingerprint` says whether the database underneath changed. If it did, every cached query
 * this client holds describes a database that no longer exists, so the cache is cleared outright
 * rather than invalidated — invalidating would leave the stale results on screen until each
 * refetch landed, which on a restored-from-last-week database means showing numbers that were
 * never true.
 *
 * Two things it deliberately does not cover: an app with no backend URL configured yet (a setup
 * state, not an outage), and the Settings route (where the connection is fixed and a staged
 * restore is cancelled). Covering either would make a recoverable situation unrecoverable from
 * inside the app.
 */
export function MaintenanceOverlay() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const lastFingerprint = useRef<string | null>(null);
  const onSettings = useRouterState({ select: (state) => state.location.pathname.startsWith("/settings") });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        // No backend configured at all is a setup state, not an outage — a first-run thin
        // client hasn't been told where the host is yet. Treating it as an outage would put a
        // full-screen overlay over the app before it has ever worked.
        const { backendUrl } = await getSettings();
        if (!backendUrl) {
          if (!cancelled) {
            setUnreachable(false);
            timer = setTimeout(poll, POLL_INTERVAL_MS);
          }
          return;
        }

        const next = await fetchSystemStatus<SystemStatus>();
        if (cancelled) return;
        setUnreachable(false);

        const previous = lastFingerprint.current;
        lastFingerprint.current = next.data_fingerprint;

        // A changed fingerprint means a different database, not merely newer rows.
        if (previous !== null && previous !== next.data_fingerprint) {
          queryClient.clear();
        } else if (previous !== null && status?.status === "maintenance" && next.status === "ok") {
          // Same database, but we were locked out and may have missed writes.
          queryClient.invalidateQueries();
        }

        setStatus(next);
      } catch {
        if (cancelled) return;
        // Can't tell "restore in progress" from "backend down" here, and the message is honest
        // for both — the app is unreachable and will reconnect by itself.
        setUnreachable(true);
      }
      if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL_MS);
    };

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // Deliberately mounted once: the loop reschedules itself and reads fresh state through refs
    // and the setState callback, so re-running it on every status change would stack timers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);

  const inMaintenance = status?.status === "maintenance";
  if (!inMaintenance && !unreachable) return null;

  // Never cover Settings. It holds the Connection tab — the one place a wrong or missing backend
  // URL can be corrected — and also the Cancel-restore button. An overlay that hid either would
  // turn a recoverable problem into a stuck app with no way out from inside it.
  if (onSettings) return null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/60 p-4">
      <div className="flex max-w-sm flex-col gap-2 rounded bg-white p-6 text-center shadow-lg">
        <p className="text-lg font-semibold">
          {inMaintenance ? "Restoring a backup" : "Reconnecting"}
        </p>
        <p className="text-sm text-slate-600">
          {inMaintenance
            ? "StockSmith is restoring a backup on the host computer. This device will reconnect on its own."
            : "Can't reach StockSmith right now. This will clear as soon as it's back."}
        </p>
      </div>
    </div>
  );
}
