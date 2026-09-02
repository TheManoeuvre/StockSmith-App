import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { platformsApi, type SyncGap } from "../../api/platforms";
import { getAutostartEnabled, isDesktopApp, setAutostartEnabled } from "../../lib/tauri";
import { ErrorBanner } from "../common/ErrorBanner";
import { SettingsCard } from "./SettingsCard";

const WINDOW_DAYS = 7;
// Enough to see the shape of a bad week without turning the panel into a log viewer. The
// totals above the list already carry the summary, and every gap is in the sync log anyway.
const MAX_GAPS_LISTED = 5;

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours < 24) return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const leftoverHours = hours % 24;
  return leftoverHours ? `${days}d ${leftoverHours}h` : `${days}d`;
}

function formatGapRange(gap: SyncGap): string {
  const from = new Date(gap.started_at);
  const to = new Date(gap.ended_at);
  const sameDay = from.toDateString() === to.toDateString();
  const fromLabel = from.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const toLabel = sameDay
    ? to.toLocaleTimeString(undefined, { timeStyle: "short" })
    : to.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return `${fromLabel} – ${toLabel}`;
}

/**
 * Whether StockSmith keeps running when the window is closed, and whether it actually did.
 *
 * The two halves belong together: the first is a promise ("it stays running"), the second is
 * the only thing that can check it. Uptime nobody measures is a belief, and a night with no
 * sync used to look exactly like a night with no orders (docs/plan-background-sync.md §7).
 */
export function BackgroundSyncSettings() {
  const queryClient = useQueryClient();
  const desktop = isDesktopApp();

  const { data: autostart } = useQuery({
    queryKey: ["settings", "autostart"],
    queryFn: getAutostartEnabled,
    enabled: desktop,
  });

  const autostartMutation = useMutation({
    // Wrapped rather than passed by reference: react-query hands mutationFn a second
    // argument of its own, and forwarding that into the Tauri bridge is an accident waiting
    // to matter the day the bridge grows a second parameter.
    mutationFn: (enabled: boolean) => setAutostartEnabled(enabled),
    onSuccess: (actual) => queryClient.setQueryData(["settings", "autostart"], actual),
  });

  const { data: health } = useQuery({
    queryKey: ["platforms", "sync-health", WINDOW_DAYS],
    queryFn: () => platformsApi.syncHealth(WINDOW_DAYS),
  });

  // The write succeeded but Windows didn't end up in the state we asked for. Rare, and
  // exactly what plugins-workspace#771 describes, so it says what it saw rather than
  // pretending the toggle worked.
  const writeDidNotTake =
    autostartMutation.isSuccess && autostartMutation.data !== autostartMutation.variables;

  return (
    <SettingsCard
      title="Background syncing"
      help="Closing the window leaves StockSmith running in the notification area, so orders keep importing and stock keeps pushing to Etsy and eBay. Use Quit on its tray icon to stop it properly."
    >
      {desktop && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={autostart ?? false}
            disabled={autostart === undefined || autostartMutation.isPending}
            onChange={(e) => autostartMutation.mutate(e.target.checked)}
          />
          <span>
            <span className="font-medium">Start StockSmith when I sign in to Windows</span>
            <span className="block text-slate-500">
              It opens straight to the tray without a window. Note this happens at sign-in, not at
              power-on — after a Windows Update restart it only runs once someone signs in, unless
              Windows is set to sign you back in automatically.
            </span>
          </span>
        </label>
      )}

      {writeDidNotTake && (
        <p className="text-sm text-amber-700">
          Windows didn't keep that setting. Check Startup Apps in Windows Settings — something may
          be turning it off.
        </p>
      )}
      <ErrorBanner error={autostartMutation.error} />

      <div className="border-t border-slate-100 pt-3">
        <h3 className="text-sm font-medium">Sync coverage, last {WINDOW_DAYS} days</h3>
        {!health && <p className="mt-1 text-sm text-slate-500">Checking…</p>}

        {health && !health.measurable && <p className="mt-1 text-sm text-slate-500">{health.reason}</p>}

        {health?.measurable && health.gaps.length === 0 && (
          <p className="mt-1 text-sm text-slate-600">
            No gaps — StockSmith has been syncing throughout.
          </p>
        )}

        {health?.measurable && health.gaps.length > 0 && (
          <>
            <p className="mt-1 text-sm text-slate-600">
              {health.gaps.length} {health.gaps.length === 1 ? "gap" : "gaps"} totalling{" "}
              {formatDuration(health.total_gap_minutes)}, longest{" "}
              {formatDuration(health.longest_gap_minutes)}. StockSmith wasn't running then, so
              anything sold in that time only appeared once it started again.
            </p>
            <ul className="mt-2 flex flex-col gap-0.5 text-sm text-slate-500">
              {health.gaps
                .slice()
                // Longest first: on a bad week the six-hour outage is the story, and it would
                // otherwise be buried among a run of short ones.
                .sort((a, b) => b.minutes - a.minutes)
                .slice(0, MAX_GAPS_LISTED)
                .map((gap) => (
                  <li key={`${gap.started_at}-${gap.ended_at}`}>
                    {formatGapRange(gap)} ({formatDuration(gap.minutes)})
                  </li>
                ))}
            </ul>
            {health.gaps.length > MAX_GAPS_LISTED && (
              <p className="mt-1 text-xs text-slate-400">
                …and {health.gaps.length - MAX_GAPS_LISTED} shorter.
              </p>
            )}
          </>
        )}
      </div>
    </SettingsCard>
  );
}
