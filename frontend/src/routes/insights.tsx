import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { insightsApi, type InsightsChannel, type InsightsGranularity, type InsightsScope } from "../api/insights";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { FilterTabs } from "../components/common/FilterTabs";
import { SegmentedControl } from "../components/common/SegmentedControl";
import { CostsTab } from "../components/insights/CostsTab";
import { OverviewTab } from "../components/insights/OverviewTab";
import { EmptyState, TilesSkeleton, ToggleChip } from "../components/insights/parts";
import { ProductsTab } from "../components/insights/ProductsTab";
import { QUALITY_NOTES, QualityTab } from "../components/insights/QualityTab";
import { CHANNEL_LABELS, RANGE_PRESETS, resolveRange, type RangePreset } from "../lib/insights";

export const Route = createFileRoute("/insights")({
  component: InsightsPage,
});

type InsightsTab = "overview" | "products" | "costs" | "quality";

/** Channels offered as filters. Shopify has no adapter yet, so it is never selectable; the
 *  backend still counts it if it ever appears. */
const CHANNELS: InsightsChannel[] = ["etsy", "ebay", "square", "manual"];

const GRANULARITIES: { value: InsightsGranularity; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
];

function InsightsPage() {
  const [tab, setTab] = useState<InsightsTab>("overview");
  const [preset, setPreset] = useState<RangePreset>("90d");
  const [compareOn, setCompareOn] = useState(true);
  const [selected, setSelected] = useState<InsightsChannel[]>(CHANNELS);
  const [granularity, setGranularity] = useState<InsightsGranularity>("week");
  const [includeFailedBuilds, setIncludeFailedBuilds] = useState(true);
  const [confidenceOpen, setConfidenceOpen] = useState(false);

  // Fixed for the life of the page so the query keys don't churn as the clock ticks over.
  const today = useMemo(() => new Date(), []);
  const range = useMemo(() => resolveRange(preset, today), [preset, today]);

  const allSelected = selected.length === CHANNELS.length;
  const scope: InsightsScope = useMemo(
    () => ({ start: range.start, end: range.end, channels: allSelected ? null : selected, includeFailedBuilds }),
    [range.start, range.end, allSelected, selected, includeFailedBuilds],
  );
  const hasChannels = selected.length > 0;
  const compare = compareOn ? range.compare : null;

  const overview = useQuery({
    queryKey: ["insights", "overview", scope, granularity, compare],
    queryFn: () => insightsApi.overview(scope, granularity, compare),
    enabled: hasChannels,
    placeholderData: keepPreviousData,
  });
  // "This year" draws last year's line right through to December, which the like-for-like
  // comparison above (same dates last year) stops short of.
  const priorYear = useQuery({
    queryKey: ["insights", "overview-prior-year", scope, granularity, range.previousFullYear],
    queryFn: () => insightsApi.overview({ ...scope, ...range.previousFullYear! }, granularity, null),
    enabled: hasChannels && compareOn && range.previousFullYear !== null,
  });
  const quality = useQuery({
    queryKey: ["insights", "quality", range.start, range.end, scope.channels],
    queryFn: () => insightsApi.quality(scope),
    enabled: hasChannels,
  });

  function toggleChannel(channel: InsightsChannel) {
    setSelected((cur) => (cur.includes(channel) ? cur.filter((c) => c !== channel) : CHANNELS.filter((c) => c === channel || cur.includes(c))));
  }

  const flagged = quality.data?.flagged_total ?? 0;
  const noSales = overview.data !== undefined && overview.data.totals.orders === 0;

  return (
    <div className="mx-auto max-w-[1200px]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Insights</h1>
          <p className="text-xs text-slate-500">{range.label} · all figures by order placed date</p>
        </div>
        <div className="relative">
          <button
            type="button"
            aria-expanded={confidenceOpen}
            onClick={() => setConfidenceOpen((o) => !o)}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium ${flagged > 0 ? "border-amber-300 bg-amber-100 text-amber-800" : "border-slate-300 bg-white text-slate-600"}`}
          >
            {flagged > 0 ? `⚠ Data confidence: ${flagged} flagged` : "Data confidence: all clear"}
          </button>
          {confidenceOpen && quality.data && (
            <div role="dialog" aria-label="Data confidence" className="absolute right-0 top-9 z-20 w-80 rounded-lg border border-slate-200 bg-white p-3.5 shadow-lg">
              <div className="mb-2 flex items-center justify-between">
                <b className="text-[13px]">Data confidence</b>
                <button type="button" onClick={() => setConfidenceOpen(false)} className="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50">
                  Close
                </button>
              </div>
              <ul className="flex flex-col gap-1.5 text-[12.5px]">
                {QUALITY_NOTES.map((n) => (
                  <li key={n.key} className="flex justify-between">
                    <span>{n.label}</span>
                    <b className="tabular-nums">{quality.data![n.key]}</b>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={() => {
                  setTab("quality");
                  setConfidenceOpen(false);
                }}
                className="mt-3 rounded border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50"
              >
                See details
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="sticky top-0 z-10 -mx-6 mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 bg-slate-50 px-6 py-2.5">
        <SegmentedControl ariaLabel="Date range" options={RANGE_PRESETS} value={preset} onChange={setPreset} />
        <ToggleChip pressed={compareOn} onClick={() => setCompareOn((c) => !c)} title={preset === "year" ? "Compared with the same dates last year" : "Compared with the period just before"}>
          Compare to previous period
        </ToggleChip>
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Channels">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">Channel</span>
          {CHANNELS.map((c) => (
            <ToggleChip key={c} pressed={selected.includes(c)} onClick={() => toggleChannel(c)}>
              {CHANNEL_LABELS[c]}
            </ToggleChip>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[10.5px] font-semibold uppercase tracking-wider text-slate-400">By</span>
          <SegmentedControl ariaLabel="Group by" options={GRANULARITIES} value={granularity} onChange={setGranularity} />
        </div>
        <ToggleChip
          pressed={includeFailedBuilds}
          onClick={() => setIncludeFailedBuilds((v) => !v)}
          title="Failed builds burn materials without producing sellable stock. When on, that cost is deducted from net profit and margin."
        >
          Failed builds in profit
        </ToggleChip>
      </div>

      <FilterTabs
        active={tab}
        onChange={(id) => setTab(id as InsightsTab)}
        tabs={[
          { id: "overview", label: "Overview" },
          { id: "products", label: "Products" },
          { id: "costs", label: "Costs" },
          { id: "quality", label: "Data quality", count: flagged || undefined },
        ]}
      />

      {!hasChannels ? (
        <EmptyState
          title="No channels selected"
          action={
            <button type="button" onClick={() => setSelected(CHANNELS)} className="rounded border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50">
              Select all channels
            </button>
          }
        >
          Pick at least one channel to see figures.
        </EmptyState>
      ) : (
        <>
          <ErrorBanner error={overview.error} />
          {tab === "overview" && overview.isLoading && <TilesSkeleton />}
          {tab === "overview" && overview.data && !noSales && (
            <OverviewTab
              overview={overview.data}
              priorYearTrend={priorYear.data?.trend ?? null}
              range={range}
              compareOn={compareOn}
              granularity={granularity}
              onSoloChannel={(channel) => setSelected([channel])}
            />
          )}
          {noSales && (tab === "overview" || tab === "products" || tab === "costs") && (
            <EmptyState
              title={`No orders between ${range.start} and ${range.end}`}
              action={
                <button type="button" onClick={() => setPreset("90d")} className="rounded border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50">
                  Reset to last 90 days
                </button>
              }
            >
              Try a longer range, or include more channels.
            </EmptyState>
          )}
          {tab === "products" && !noSales && <ProductsTab scope={scope} granularity={granularity} />}
          {tab === "costs" && !noSales && <CostsTab scope={scope} />}
          {tab === "quality" && (quality.data ? <QualityTab quality={quality.data} /> : <TilesSkeleton />)}
        </>
      )}
    </div>
  );
}
