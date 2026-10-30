import { useQuery } from "@tanstack/react-query";
import { insightsApi, type InsightsChannelCosts, type InsightsScope } from "../../api/insights";
import { CHANNEL_LABELS, count, gbp, num, pct } from "../../lib/insights";
import { ErrorBanner } from "../common/ErrorBanner";
import { BarList, CHART_COLORS, StackedBar, Waterfall, type WaterfallStep } from "./charts";
import { Card, EmptyState, SectionHead, Skeleton } from "./parts";

/** One channel's costs as shares of its revenue. Failed builds, packaging and resends are
 *  small next to the rest, so they share one "Other" segment. */
export function costStack(c: InsightsChannelCosts) {
  const revenue = num(c.revenue) ?? 0;
  if (revenue <= 0) return [];
  const share = (v: string) => ((num(v) ?? 0) / revenue) * 100;
  const other = share(c.packaging) + share(c.resends) + share(c.failed_builds);
  return [
    { key: "fees", label: "Fees", value: share(c.fees), color: "#6d28d9" },
    { key: "postage", label: "Postage", value: share(c.postage), color: "#0369a1" },
    { key: "materials", label: "Materials", value: share(c.materials), color: "#475569" },
    { key: "other", label: "Other", value: other, color: "#b45309" },
    { key: "profit", label: "Profit", value: Math.max(share(c.net_profit), 0), color: CHART_COLORS.warm },
  ];
}

export function CostsTab({ scope }: { scope: InsightsScope }) {
  const costs = useQuery({ queryKey: ["insights", "costs", scope], queryFn: () => insightsApi.costs(scope) });
  const data = costs.data;

  if (costs.isLoading) return <Skeleton className="mt-4 h-72 w-full" />;
  if (costs.error) return <div className="mt-4"><ErrorBanner error={costs.error} /></div>;
  if (!data) return null;

  const gross = data.waterfall.find((c) => c.key === "gross");
  if (!gross || (num(gross.amount) ?? 0) === 0) {
    return <EmptyState title="No sales in this period">Widen the date range or include more channels.</EmptyState>;
  }

  const steps: WaterfallStep[] = data.waterfall.map((c) => ({
    key: c.key,
    label: c.label,
    amount: num(c.amount) ?? 0,
    kind: c.key === "gross" ? "start" : c.key === "net_profit" ? "end" : "cost",
  }));
  const net = data.waterfall.find((c) => c.key === "net_profit");
  const failedPercent = num(data.failed_builds_percent_of_materials);
  const failedTotal = num(data.failed_builds_total) ?? 0;

  return (
    <>
      <SectionHead title="Where the money goes" sub="From gross takings down to net profit" />
      <Card className="p-3">
        <Waterfall steps={steps} ariaLabel={`Waterfall from gross takings ${gbp(gross.amount, 0)} down to net profit ${gbp(net?.amount ?? 0, 0)}`} />
        <p className="px-1 text-[11.5px] text-slate-400">
          Net profit = revenue − platform fees − postage cost − materials − packaging − resend postage − resend costs
          {data.include_failed_builds ? " − failed builds" : ""}.
        </p>
      </Card>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        <Card className="p-3.5">
          <p className="mb-2.5 text-[13px] font-semibold">Materials cost by category</p>
          <BarList
            color={CHART_COLORS.accent}
            rows={data.materials_by_category.map((m) => ({ key: m.key, label: m.label, value: num(m.amount) ?? 0, text: gbp(m.amount, 0) }))}
          />
          <p className="mt-2 text-[11px] text-slate-400">Split by each product's current recipe; the amounts are the costs frozen on the orders.</p>
        </Card>
        <Card className="p-3.5">
          <p className="mb-2.5 text-[13px] font-semibold">Resends by reason</p>
          <BarList
            color={CHART_COLORS.warm}
            rows={data.resends_by_reason.map((m) => ({ key: m.key, label: m.label, value: num(m.amount) ?? 0, text: gbp(m.amount, 0) }))}
          />
        </Card>
      </div>

      <Card className="mt-3 p-3.5">
        <p className="mb-2.5 text-[13px] font-semibold">Cost stack per £100 of revenue, by channel</p>
        <div className="flex flex-col gap-2.5">
          {data.channels.map((c) => {
            const segments = costStack(c);
            const aria = `${CHANNEL_LABELS[c.channel]}: ${segments.map((s) => `${s.label} ${s.value.toFixed(0)}`).join(", ")}`;
            return (
              <div key={c.channel} className="grid grid-cols-[70px_1fr] items-center gap-2.5 text-[12.5px]">
                <span>{CHANNEL_LABELS[c.channel]}</span>
                <StackedBar segments={segments} ariaLabel={aria} />
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-slate-400">Fees, postage, materials, other (packaging, resends, failed builds), then profit. Labelled, not just coloured.</p>
      </Card>

      <SectionHead
        title="Failed builds"
        sub={
          failedTotal > 0
            ? `${gbp(failedTotal)} of material went on failed builds${failedPercent !== null ? ` (${pct(failedPercent)} of all material used)` : ""}, charged to the units sold in this period`
            : "No failed builds are costed against sales in this period"
        }
      />
      {data.failed_builds.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px] tabular-nums">
              <thead>
                <tr className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">
                  <th className="p-2 text-left">Product</th>
                  {["Fail rate", "Wasted materials", "Extra cost per good unit", "Margin before", "Margin after"].map((h) => (
                    <th key={h} className="p-2 text-right">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.failed_builds.map((p) => {
                  const rate = num(p.fail_rate_percent);
                  return (
                    <tr key={p.key} className="border-t border-slate-100">
                      <td className="max-w-[300px] truncate p-2" title={p.name}>
                        {p.name}
                      </td>
                      <td className="p-2 text-right">{pct(p.fail_rate_percent)}</td>
                      <td className="p-2 text-right">{gbp(p.failed_build_cost)}</td>
                      <td className="p-2 text-right">{rate !== null && rate < 100 ? `+${((rate / (100 - rate)) * 100).toFixed(1)}%` : "-"}</td>
                      <td className="p-2 text-right">{pct(p.margin_before_failed_builds_percent)}</td>
                      <td className="p-2 text-right">{pct(p.margin_percent)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="px-3.5 py-2.5 text-[11.5px] text-slate-400">
            From build records: failed quantity × the materials recorded as used, at the cost frozen when the build was logged. {count(data.failed_builds.length)} products shown, most wasted first.
          </p>
        </Card>
      )}
    </>
  );
}
