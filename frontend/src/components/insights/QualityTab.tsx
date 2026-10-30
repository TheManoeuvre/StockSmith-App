import { Link } from "@tanstack/react-router";
import type { InsightsQuality } from "../../api/insights";
import { CHANNEL_LABELS, count, formatDate, ISSUE_LABELS } from "../../lib/insights";
import { Card, InsightsStat, SectionHead } from "./parts";

export const QUALITY_NOTES: { key: keyof Pick<InsightsQuality, "cogs_pending" | "postage_missing" | "financials_not_synced" | "unmapped_lines">; label: string; why: string }[] = [
  { key: "cogs_pending", label: "COGS pending", why: "A line has no cost snapshot yet, so its cost reads as nothing" },
  { key: "postage_missing", label: "Postage missing", why: "Shipped with no postage cost recorded" },
  { key: "financials_not_synced", label: "Financials not synced", why: "Profit is left out of the totals, not counted as zero" },
  { key: "unmapped_lines", label: "Unmapped SKUs", why: "Sold units with no product, so no product margin" },
];

export function QualityTab({ quality }: { quality: InsightsQuality }) {
  return (
    <>
      <SectionHead title="Data quality" sub="These orders are in the figures with the gaps noted, never silently as zero" />
      <div className="grid grid-cols-2 gap-2.5 xl:grid-cols-4">
        {QUALITY_NOTES.map((n) => (
          <InsightsStat key={n.key} label={n.label} value={count(quality[n.key])} note={n.why} />
        ))}
      </div>
      <Card className="mt-3">
        {quality.flagged.length === 0 ? (
          <p className="px-3.5 py-6 text-center text-xs text-slate-500">No orders in this period need attention.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">
                  <th className="p-2 text-left">Order</th>
                  <th className="p-2 text-left">Channel</th>
                  <th className="p-2 text-left">Placed</th>
                  <th className="p-2 text-left">Issue</th>
                  <th className="p-2" />
                </tr>
              </thead>
              <tbody>
                {quality.flagged.map((o) => (
                  <tr key={o.order_id} className="border-t border-slate-100">
                    <td className="p-2 font-mono text-[11.5px]">{o.external_order_id ?? `#${o.order_id}`}</td>
                    <td className="p-2">{CHANNEL_LABELS[o.channel]}</td>
                    <td className="p-2">{formatDate(o.placed_on)}</td>
                    <td className="p-2">
                      <span className="flex flex-wrap gap-1">
                        {o.issues.map((i) => (
                          <span key={i} className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">
                            {ISSUE_LABELS[i] ?? i}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td className="p-2 text-right">
                      <Link to="/orders/$orderId" params={{ orderId: String(o.order_id) }} className="text-blue-700 hover:underline">
                        Open in Orders →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {quality.flagged_total > quality.flagged.length && (
          <p className="px-3.5 pb-3 text-[11.5px] text-slate-400">
            Showing the {quality.flagged.length} most recent of {quality.flagged_total} flagged orders.
          </p>
        )}
      </Card>
    </>
  );
}
