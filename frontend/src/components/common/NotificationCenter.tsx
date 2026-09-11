import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { notificationsApi, type Notification } from "../../api/notifications";

// Fast enough to feel live for something the user is meant to notice soon, cheap enough (one
// COUNT(*) query) to poll from every page — this sits in the root layout like
// SyncStatusIndicator, just on a shorter interval since a rising unread count is the thing
// itself, not a summary of something slower-moving.
const POLL_INTERVAL_MS = 20_000;
const LIST_PAGE_SIZE = 30;

/** "4m ago" / "3h ago" / "2d ago", matching SyncStatusIndicator's formatRelative. */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function NotificationRow({ notification, onRead }: { notification: Notification; onRead: (id: number) => void }) {
  const unread = notification.read_at === null;
  return (
    <button
      type="button"
      onClick={() => unread && onRead(notification.id)}
      className={`flex w-full flex-col gap-0.5 px-3 py-2 text-left text-[12.5px] hover:bg-slate-50 ${
        unread ? "bg-blue-50/60" : ""
      }`}
    >
      <div className="flex items-start gap-2">
        {unread && <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-600" aria-hidden="true" />}
        <span className={`flex-1 ${unread ? "font-medium text-slate-900" : "text-slate-600"}`}>
          {notification.title}
        </span>
      </div>
      <p className={`${unread ? "pl-3.5" : ""} text-slate-500`}>{notification.body}</p>
      <span className={`${unread ? "pl-3.5" : ""} text-[11px] text-slate-400`}>
        {formatRelative(notification.created_at)}
      </span>
    </button>
  );
}

export function NotificationCenter() {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    const onClickAway = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, []);

  const { data: unread } = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: notificationsApi.unreadCount,
    refetchInterval: POLL_INTERVAL_MS,
    retry: false,
  });

  const { data: page } = useQuery({
    queryKey: ["notifications", "list"],
    queryFn: () => notificationsApi.list({ limit: LIST_PAGE_SIZE }),
    enabled: open,
    refetchInterval: open ? POLL_INTERVAL_MS : false,
  });

  const markReadMutation = useMutation({
    mutationFn: (id: number) => notificationsApi.markRead(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: () => notificationsApi.markAllRead(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const count = unread?.count ?? 0;

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={count > 0 ? `${count} unread notifications` : "Notifications"}
        className={`flex w-full items-center gap-2 rounded-md px-[9px] py-[7px] text-left text-[13px] font-medium hover:bg-slate-100 ${
          open ? "bg-slate-100 text-slate-900" : "text-slate-600"
        }`}
      >
        <span className="flex-1">Alerts</span>
        {count > 0 && (
          <span className="rounded px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums text-red-800 bg-red-100">
            {count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-30 mb-1 w-[340px] rounded-md border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-[12.5px] font-semibold text-slate-900">Notifications</span>
            <button
              type="button"
              onClick={() => markAllReadMutation.mutate()}
              disabled={count === 0 || markAllReadMutation.isPending}
              className="text-[11.5px] text-slate-500 underline disabled:cursor-not-allowed disabled:text-slate-300 disabled:no-underline"
            >
              Mark all read
            </button>
          </div>
          <div className="max-h-[360px] overflow-y-auto divide-y divide-slate-100">
            {!page && <p className="px-3 py-4 text-center text-[12px] text-slate-500">Loading…</p>}
            {page && page.items.length === 0 && (
              <p className="px-3 py-4 text-center text-[12px] text-slate-500">You're all caught up.</p>
            )}
            {page?.items.map((notification) => (
              <NotificationRow
                key={notification.id}
                notification={notification}
                onRead={(id) => markReadMutation.mutate(id)}
              />
            ))}
          </div>
          <Link
            to="/settings"
            search={{ page: "notifications" }}
            onClick={() => setOpen(false)}
            className="block border-t border-slate-100 px-3 py-2 text-center text-[11.5px] text-slate-500 hover:bg-slate-50"
          >
            Notification settings
          </Link>
        </div>
      )}
    </div>
  );
}
