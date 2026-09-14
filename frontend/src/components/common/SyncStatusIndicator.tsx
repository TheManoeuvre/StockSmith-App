import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { platformsApi, type PlatformSyncSummary } from "../../api/platforms";
import { PLATFORM_LABELS } from "../../lib/platforms";

// Slow on purpose. Nothing here changes faster than the sync interval (15 min by
// default), and this sits in the root layout, so it runs on every page in the app.
const POLL_INTERVAL_MS = 60_000;

/** "4m ago" / "3h ago" / "2d ago". Falls back to the absolute date past a week, where a
 *  relative figure stops being easier to read than the date itself. */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  // Clock skew (or a sync that landed mid-poll) can put the timestamp slightly in the
  // future; "in 3 seconds" would look broken, so anything not yet past reads as "just now".
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function summarise(summaries: PlatformSyncSummary[]) {
  const connected = summaries.filter((s) => s.connected);
  const errored = connected.filter((s) => s.last_sync_status === "error");
  const failingPushes = connected.reduce((total, s) => total + s.failing_push_count, 0);
  // The most recent successful-or-not sync across platforms — the question the label
  // answers is "how stale is my data", which is governed by whichever synced last.
  // Compared as parsed timestamps rather than raw strings, so this doesn't quietly
  // depend on every platform's timestamp arriving in the same offset notation.
  const latest = connected.reduce<string | null>((newest, s) => {
    if (s.last_sync_at === null) return newest;
    if (newest === null) return s.last_sync_at;
    return new Date(s.last_sync_at) > new Date(newest) ? s.last_sync_at : newest;
  }, null);
  return { connected, errored, failingPushes, latest };
}

export function SyncStatusIndicator() {
  const { data } = useQuery({
    queryKey: ["platforms", "sync-summary"],
    queryFn: () => platformsApi.syncSummary(),
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });

  // Render nothing at all until there's something true to say — an indicator that shows
  // "never synced" while the first request is still in flight would be actively wrong.
  if (!data) return null;

  const { connected, errored, failingPushes, latest } = summarise(data);
  if (connected.length === 0) return null;

  const hasProblem = errored.length > 0 || failingPushes > 0;
  const problems = [
    ...errored.map((s) => `${PLATFORM_LABELS[s.platform]} sync failed: ${s.last_sync_error ?? "unknown error"}`),
    ...connected
      .filter((s) => s.failing_push_count > 0)
      .map(
        (s) =>
          `${PLATFORM_LABELS[s.platform]}: ${s.failing_push_count} listing(s) failed to receive a stock update`
      ),
  ];

  return (
    <Link
      to="/settings"
      title={
        hasProblem
          ? problems.join("\n")
          : connected
              .map(
                (s) =>
                  `${PLATFORM_LABELS[s.platform]}: ${s.last_sync_at ? formatRelative(s.last_sync_at) : "never synced"}`
              )
              .join("\n")
      }
      className="flex w-full items-center gap-2 rounded-md px-[9px] py-[7px] text-[12.5px] hover:bg-slate-100"
    >
      {/* Not colour alone — the glyph carries the same meaning for anyone who can't
          distinguish red from green, and this is the app's only passive failure signal. */}
      <span
        className={`flex h-[18px] min-w-[18px] flex-none items-center justify-center rounded-full text-[10.5px] font-medium text-white ${
          hasProblem ? "bg-red-600" : "bg-green-600"
        }`}
      >
        {hasProblem ? "!" : "✓"}
      </span>
      <span className={`flex-1 ${hasProblem ? "text-red-700" : "text-slate-600"}`}>
        {latest ? `Synced ${formatRelative(latest)}` : "Never synced"}
      </span>
    </Link>
  );
}
