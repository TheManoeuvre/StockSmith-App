import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { platformsApi } from "../../api/platforms";
import type { ListingPlatform } from "../../api/types";
import { formatDayMonth } from "../../lib/format";

const INITIAL_LIMIT = 5;

/**
 * Recent outbound quantity pushes for this product on one platform — reads the existing
 * `listing-push-log` endpoint (previously wired but unused) and filters it to this product.
 */
export function SyncLog({
  productId,
  platform,
}: {
  productId: number;
  platform: ListingPlatform;
}) {
  const [expanded, setExpanded] = useState(false);
  // The endpoint is shop-wide; pull a generous page and narrow to this product client-side.
  const { data } = useQuery({
    queryKey: ["platforms", platform, "listing-push-log", productId],
    queryFn: () => platformsApi.listingPushLog(platform, 100, 0),
  });

  const rows = (data?.items ?? []).filter((r) => r.product_id === productId);
  if (rows.length === 0) return null;

  const visible = expanded ? rows : rows.slice(0, INITIAL_LIMIT);

  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-medium text-slate-600">Sync log</h4>
      <ul className="flex flex-col gap-1 text-sm">
        {visible.map((r) => (
          <li key={r.id} className="flex items-center gap-2">
            <span className="text-xs tabular-nums text-slate-400">
              {formatDayMonth(r.attempted_at)}{" "}
              {new Date(r.attempted_at).toLocaleTimeString(undefined, {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span className="min-w-0 flex-1 truncate">
              {r.variant_name ?? r.product_name ?? "—"} · qty {r.attempted_qty}
            </span>
            <span
              className={`rounded px-2 py-0.5 text-xs ${
                r.status === "success"
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-rose-100 text-rose-800"
              }`}
              title={r.error_message ?? undefined}
            >
              {r.status === "success" ? "ok" : "failed"}
            </span>
          </li>
        ))}
      </ul>
      {rows.length > INITIAL_LIMIT && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="self-start rounded border border-slate-300 px-3 py-1 text-xs"
        >
          {expanded ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}
