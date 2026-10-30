import { useMemo, useState } from "react";
import type { InsightsBucket, InsightsChannel, InsightsGranularity, InsightsOverview, InsightsTotals } from "../../api/insights";
import {
  axisLabels,
  bucketSlots,
  CHANNEL_LABELS,
  count,
  formatDate,
  gbp,
  gbpCompact,
  num,
  pct,
  percentChange,
  pointChange,
  saveCsv,
  toCsv,
  type ResolvedRange,
} from "../../lib/insights";
import { SegmentedControl } from "../common/SegmentedControl";
import { CHART_COLORS, LineChart, StackedBar, type LineSeries } from "./charts";
import { Card, InsightsStat, PendingBadge, SectionHead } from "./parts";

type Metric = "orders" | "units" | "revenue" | "profit" | "margin";

const METRICS: { value: Metric; label: string }[] = [
  { value: "orders", label: "Orders" },
  { value: "units", label: "Units" },
  { value: "revenue", label: "Revenue" },
  { value: "profit", label: "Profit" },
  { value: "margin", label: "Margin %" },
];

const METRIC_NAMES: Record<Metric, string> = {
  orders: "Orders",
  units: "Units",
  revenue: "Revenue",
  profit: "Net profit",
  margin: "Net margin",
};

function metricValue(bucket: InsightsBucket, metric: Metric): number | null {
  switch (metric) {
    case "orders":
      return bucket.orders;
    case "units":
      return bucket.units;
    case "revenue":
      return num(bucket.revenue);
    case "profit":
      return num(bucket.net_profit);
    case "margin":
      return num(bucket.margin_percent);
  }
}

function formatMetric(metric: Metric, value: number): string {
  if (metric === "margin") return `${value.toFixed(1)}%`;
  if (metric === "revenue" || metric === "profit") return gbp(value);
  return count(value);
}

function axisFormat(metric: Metric, value: number): string {
  if (metric === "margin") return `${Math.round(value)}%`;
  if (metric === "revenue" || metric === "profit") return gbpCompact(value);
  return count(value);
}

/** The channel colours in the share bar. Each also carries its name, so colour isn't the key. */
const CHANNEL_COLORS: Record<InsightsChannel, string> = {
  etsy: "#2563eb",
  ebay: "#c2410c",
  square: "#475569",
  manual: "#7c8aa0",
  shopify: "#0f766e",
};

function slotTitle(slot: string, granularity: InsightsGranularity): string {
  if (granularity === "month") {
    const [y, m] = slot.split("-");
    return `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1]} ${y}`;
  }
  return granularity === "week" ? `Week of ${formatDate(slot, true)}` : formatDate(slot, true);
}

export function buildTiles(totals: InsightsTotals, previous: InsightsTotals | null, againstLabel: string) {
  const cur = (v: string | null) => num(v);
  const prev = (pick: (t: InsightsTotals) => string | number | null) => (previous ? num(pick(previous)) : null);
  const note = previous ? againstLabel : undefined;
  const profitPerOrder = (t: InsightsTotals) => t.average_profit_per_order;

  return [
    {
      key: "orders",
      label: "Orders",
      value: count(totals.orders),
      delta: percentChange(totals.orders, prev((t) => t.orders)),
      hint: "Orders placed in the period, excluding cancelled",
      before: previous ? count(previous.orders) : null,
      note,
    },
    {
      key: "units",
      label: "Units sold",
      value: count(totals.units),
      delta: percentChange(totals.units, prev((t) => t.units)),
      hint: "Sum of ordered quantity",
      before: previous ? count(previous.units) : null,
      note,
    },
    {
      key: "revenue",
      label: "Revenue",
      value: gbp(totals.revenue),
      delta: percentChange(cur(totals.revenue), prev((t) => t.revenue)),
      hint: "Revenue = items + postage charged − refunds",
      before: previous ? gbp(previous.revenue) : null,
      note,
    },
    {
      key: "profit",
      label: "Net profit",
      value: gbp(totals.net_profit),
      delta: percentChange(cur(totals.net_profit), prev((t) => t.net_profit)),
      hint: "Net profit = revenue − platform fees − postage cost − materials − packaging − resend postage − resend costs − failed builds",
      before: previous ? gbp(previous.net_profit) : null,
      note,
    },
    {
      key: "margin",
      label: "Net margin",
      value: pct(totals.margin_percent),
      delta: pointChange(cur(totals.margin_percent), prev((t) => t.margin_percent)),
      hint: "Net profit ÷ revenue",
      before: previous ? pct(previous.margin_percent) : null,
      note,
    },
    {
      key: "aov",
      label: "Average order value",
      value: gbp(totals.average_order_value),
      delta: percentChange(cur(totals.average_order_value), prev((t) => t.average_order_value)),
      hint: "Revenue ÷ orders",
      before: previous ? gbp(previous.average_order_value) : null,
      note,
    },
    {
      key: "ppo",
      label: "Avg profit per order",
      value: gbp(profitPerOrder(totals)),
      delta: percentChange(cur(profitPerOrder(totals)), prev(profitPerOrder)),
      hint: "Net profit ÷ orders",
      before: previous ? gbp(profitPerOrder(previous)) : null,
      note,
    },
    {
      key: "refunds",
      label: "Refund rate",
      value: pct(totals.refund_rate_percent),
      delta: pointChange(cur(totals.refund_rate_percent), prev((t) => t.refund_rate_percent), true),
      hint: "Refunded amount ÷ gross takings (items + postage charged)",
      before: previous ? pct(previous.refund_rate_percent) : null,
      note,
    },
  ];
}

export function OverviewTab({
  overview,
  priorYearTrend,
  range,
  compareOn,
  granularity,
  onSoloChannel,
}: {
  overview: InsightsOverview;
  /** "This year" only: all of last year, so the comparison line can run on to December. */
  priorYearTrend: InsightsBucket[] | null;
  range: ResolvedRange;
  compareOn: boolean;
  granularity: InsightsGranularity;
  onSoloChannel: (channel: InsightsChannel) => void;
}) {
  const [metric, setMetric] = useState<Metric>("revenue");
  const isYear = range.fullYear !== null;
  const againstLabel = isYear ? `vs ${range.compare.start.slice(0, 4)}` : "vs previous period";
  const previous = compareOn ? overview.compare_totals : null;
  const tiles = useMemo(() => buildTiles(overview.totals, previous, againstLabel), [overview.totals, previous, againstLabel]);

  const slots = useMemo(
    () =>
      range.fullYear
        ? bucketSlots(range.fullYear.start, range.fullYear.end, granularity)
        : overview.trend.map((b) => b.period_start),
    [range.fullYear, overview.trend, granularity],
  );
  const dataCount = overview.trend.length;
  const comparisonBuckets = compareOn ? (isYear && priorYearTrend ? priorYearTrend : overview.compare_trend) : null;

  const series: LineSeries[] = [];
  const main = overview.trend.map((b) => metricValue(b, metric));
  const previousValues = comparisonBuckets ? slots.map((_, i) => (comparisonBuckets[i] ? metricValue(comparisonBuckets[i], metric) : null)) : null;
  if (previousValues) {
    series.push({ key: "previous", label: isYear ? range.compare.start.slice(0, 4) : "Previous", color: CHART_COLORS.muted, values: previousValues, dashed: true, direct: true });
  }
  if (metric === "revenue") {
    series.push({ key: "profit", label: "Net profit", color: CHART_COLORS.warm, values: overview.trend.map((b) => metricValue(b, "profit")), direct: true });
  }
  series.push({ key: "main", label: METRIC_NAMES[metric], color: CHART_COLORS.accent, values: main, direct: true });

  const tooltip = (index: number) => {
    const lines: { label: string; value: string; color: string }[] = [];
    const bucket = overview.trend[index];
    if (bucket) {
      const v = metricValue(bucket, metric);
      lines.push({ label: METRIC_NAMES[metric], value: v === null ? "-" : formatMetric(metric, v), color: CHART_COLORS.accent });
      if (metric === "revenue") {
        lines.push({ label: "Net profit", value: gbp(bucket.net_profit), color: CHART_COLORS.warm });
      }
    } else {
      lines.push({ label: "Not yet reached", value: "", color: CHART_COLORS.muted });
    }
    const before = previousValues?.[index];
    if (before !== null && before !== undefined) {
      const now = bucket ? metricValue(bucket, metric) : null;
      const change = metric === "margin" ? pointChange(now, before) : percentChange(now, before);
      lines.push({
        label: isYear ? `${range.compare.start.slice(0, 4)}` : "Previous period",
        value: `${formatMetric(metric, before)}${change && bucket ? ` (${change.text})` : ""}`,
        color: CHART_COLORS.muted,
      });
    }
    return { title: slotTitle(slots[index], granularity), lines };
  };

  const totalRevenue = overview.channels.reduce((sum, c) => sum + (num(c.revenue) ?? 0), 0);
  const shareText = overview.channels
    .map((c) => `${CHANNEL_LABELS[c.channel]} ${totalRevenue ? Math.round(((num(c.revenue) ?? 0) / totalRevenue) * 100) : 0}%`)
    .join(" · ");

  function exportChannels() {
    saveCsv(
      "insights-channels.csv",
      toCsv(
        ["Channel", "Orders", "Units", "Revenue", "Fees", "Net profit", "Margin %", "AOV"],
        overview.channels.map((c) => [CHANNEL_LABELS[c.channel], c.orders, c.units, c.revenue, c.platform_fees, c.net_profit, c.margin_percent, c.average_order_value]),
      ),
    );
  }

  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-2.5 xl:grid-cols-4">
        {tiles.map((tile) => (
          <InsightsStat
            key={tile.key}
            label={tile.label}
            value={tile.value}
            delta={tile.delta}
            deltaNote={tile.note}
            hint={tile.hint}
            title={tile.before ? `${tile.label}: ${tile.value} now, ${tile.before} ${tile.note}${tile.delta ? ` (${tile.delta.text})` : ""}` : `${tile.label}: ${tile.value}`}
            note={
              tile.key === "profit" && overview.totals.profit_unavailable_orders > 0 ? (
                <span className="inline-flex items-center gap-1">
                  <PendingBadge /> {overview.totals.profit_unavailable_orders} orders not yet synced
                </span>
              ) : undefined
            }
          />
        ))}
      </div>

      <SectionHead
        title="Trend"
        sub={`By ${granularity}, ${range.label}`}
        actions={<SegmentedControl ariaLabel="Trend metric" options={METRICS} value={metric} onChange={setMetric} />}
      />
      <Card className="p-3">
        <LineChart
          slots={slots}
          series={series}
          dataCount={dataCount}
          axisFormat={(v) => axisFormat(metric, v)}
          xLabels={axisLabels(slots, granularity, isYear)}
          tooltip={tooltip}
          ariaLabel={`${METRIC_NAMES[metric]} by ${granularity}, ${range.label}`}
        />
        <p className="px-1 text-[11.5px] text-slate-400">
          {compareOn ? (isYear ? `Dashed line is ${range.compare.start.slice(0, 4)}, through to December. ` : "Dashed line is the previous period. ") : ""}
          Hover or use the arrow keys to read a point.
        </p>
      </Card>

      <SectionHead
        title="By channel"
        sub="Click a row to filter the whole page to that channel"
        actions={
          <button type="button" onClick={exportChannels} className="rounded border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50">
            Export CSV
          </button>
        }
      />
      <Card>
        {overview.channels.length > 0 && (
          <div className="px-3.5 pt-3.5">
            <StackedBar
              ariaLabel={`Share of revenue: ${shareText}`}
              segments={overview.channels.map((c) => ({ key: c.channel, label: CHANNEL_LABELS[c.channel], value: num(c.revenue) ?? 0, color: CHANNEL_COLORS[c.channel] }))}
            />
            <p className="mt-1 text-[11.5px] text-slate-400">Share of revenue: {shareText}</p>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px] tabular-nums">
            <thead>
              <tr className="text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">
                <th className="p-2 text-left">Channel</th>
                {["Orders", "Units", "Revenue", "Fees", "Net profit", "Margin", "AOV"].map((h) => (
                  <th key={h} className="p-2 text-right">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {overview.channels.map((c) => (
                <tr key={c.channel} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onClick={() => onSoloChannel(c.channel)}>
                  <td className="p-2">
                    <button type="button" className="font-semibold hover:underline" onClick={(e) => { e.stopPropagation(); onSoloChannel(c.channel); }}>
                      {CHANNEL_LABELS[c.channel]}
                    </button>
                  </td>
                  <td className="p-2 text-right">{count(c.orders)}</td>
                  <td className="p-2 text-right">{count(c.units)}</td>
                  <td className="p-2 text-right">{gbp(c.revenue)}</td>
                  <td className="p-2 text-right">{gbp(c.platform_fees)}</td>
                  <td className="p-2 text-right">{gbp(c.net_profit)}</td>
                  <td className="p-2 text-right">{pct(c.margin_percent)}</td>
                  <td className="p-2 text-right">{gbp(c.average_order_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
