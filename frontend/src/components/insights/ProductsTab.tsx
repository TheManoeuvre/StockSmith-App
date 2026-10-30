import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import {
  insightsApi,
  type InsightsGranularity,
  type InsightsProductRow,
  type InsightsScope,
  type InsightsSortKey,
  type InsightsView,
} from "../../api/insights";
import { CHANNEL_LABELS, count, gbp, num, pct, saveCsv, toCsv } from "../../lib/insights";
import { ErrorBanner } from "../common/ErrorBanner";
import { SegmentedControl } from "../common/SegmentedControl";
import { Scatter, Sparkline } from "./charts";
import { Card, EmptyState, PendingBadge, SectionHead, Skeleton } from "./parts";

export const PRODUCTS_PAGE_SIZE = 10;

const VIEWS: { value: InsightsView; label: string }[] = [
  { value: "products", label: "Products" },
  { value: "variants", label: "Variants" },
  { value: "categories", label: "Categories" },
];

const COLUMNS: { key: InsightsSortKey; label: string; align: "left" | "right"; title?: string }[] = [
  { key: "name", label: "Name", align: "left" },
  { key: "units", label: "Units", align: "right" },
  { key: "fail_rate_percent", label: "Fail rate", align: "right", title: "Share of builds that failed" },
  { key: "revenue", label: "Revenue", align: "right", title: "Items + postage charged − refunds, shared across an order's lines by value" },
  { key: "cogs", label: "COGS", align: "right", title: "Materials plus packaging" },
  { key: "net_profit", label: "Net profit", align: "right" },
  { key: "margin_percent", label: "Margin", align: "right", title: "Net profit ÷ revenue" },
  { key: "profit_per_unit", label: "Profit / unit", align: "right" },
  { key: "share_of_profit_percent", label: "Share", align: "right", title: "Share of total net profit" },
];

/** Tags are relative to the typical product in view, so they flag outliers rather than
 *  applying a fixed threshold that would suit one business and not another. */
export function tagFor(row: InsightsProductRow, meanUnits: number): { text: string; tone: "amber" | "blue" | "warm" } | null {
  const profit = num(row.net_profit);
  const margin = num(row.margin_percent);
  if (profit !== null && profit < 0) return { text: "Loss-making", tone: "warm" };
  if (margin === null) return null;
  if (margin < 30 && row.units >= meanUnits) return { text: "High volume, low margin", tone: "amber" };
  if (margin > 50 && row.units < meanUnits / 2) return { text: "Low volume, high margin", tone: "blue" };
  return null;
}

const TAG_CLASSES = {
  amber: "bg-amber-100 text-amber-800",
  blue: "bg-blue-100 text-blue-700",
  warm: "bg-orange-100 text-orange-800",
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function ProductsTab({ scope, granularity }: { scope: InsightsScope; granularity: InsightsGranularity }) {
  const [view, setView] = useState<InsightsView>("products");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<InsightsSortKey>("net_profit");
  const [descending, setDescending] = useState(true);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useDebounced(search.trim(), 250);

  // Back to the first page whenever what's being listed changes.
  useEffect(() => setPage(1), [q, view, sort, descending, scope.start, scope.end, scope.channels, scope.includeFailedBuilds]);

  const request = { view, q, sort, descending, page, pageSize: PRODUCTS_PAGE_SIZE, granularity };
  const products = useQuery({
    queryKey: ["insights", "products", scope, request],
    queryFn: () => insightsApi.products(scope, request),
    placeholderData: keepPreviousData,
  });
  const scatter = useQuery({
    queryKey: ["insights", "products-scatter", scope],
    queryFn: () =>
      insightsApi.products(scope, { view: "products", q: "", sort: "net_profit", descending: true, page: 1, pageSize: 100, granularity }),
    enabled: view === "products",
  });

  const data = products.data;
  const pages = data ? Math.max(1, Math.ceil(data.total_rows / data.page_size)) : 1;
  const meanUnits = data && data.total_rows > 0 ? data.totals.units / data.total_rows : 0;
  const lastPage = data ? page >= pages : false;

  function toggleSort(key: InsightsSortKey) {
    if (key === sort) setDescending((d) => !d);
    else {
      setSort(key);
      setDescending(key !== "name");
    }
  }

  async function exportCsv() {
    const rows: InsightsProductRow[] = [];
    for (let p = 1; p <= 50; p++) {
      const chunk = await insightsApi.products(scope, { ...request, page: p, pageSize: 100 });
      rows.push(...chunk.rows);
      if (rows.length >= chunk.total_rows) break;
    }
    saveCsv(
      `insights-${view}.csv`,
      toCsv(
        ["Name", "Detail", "Units", "Fail rate %", "Revenue", "COGS", "Failed builds", "Net profit", "Margin %", "Profit per unit", "Share of profit %"],
        rows.map((r) => [r.name, r.sub, r.units, r.fail_rate_percent, r.revenue, r.cogs, r.failed_build_cost, r.net_profit, r.margin_percent, r.profit_per_unit, r.share_of_profit_percent]),
      ),
    );
  }

  const first = data && data.total_rows > 0 ? (data.page - 1) * data.page_size + 1 : 0;
  const last = data ? Math.min(data.total_rows, data.page * data.page_size) : 0;

  return (
    <>
      <SectionHead
        title="Products and variants"
        sub="Fees and postage are shared across an order's lines in proportion to their value"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search products"
              aria-label="Search products"
              className="w-48 rounded border border-slate-300 px-2.5 py-1.5 text-xs"
            />
            <SegmentedControl ariaLabel="Group by" options={VIEWS} value={view} onChange={setView} />
            <button type="button" onClick={() => void exportCsv()} className="rounded border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50">
              Export CSV
            </button>
          </div>
        }
      />
      <ErrorBanner error={products.error} />
      {products.isLoading && <Skeleton className="h-64 w-full" />}
      {data && data.total_rows === 0 && data.unmapped_units === 0 && (
        <EmptyState title={q ? `No ${view} match “${q}”` : "No sales in this period"}>
          {q ? "Try a different search." : "Widen the date range or include more channels."}
        </EmptyState>
      )}
      {data && (data.total_rows > 0 || data.unmapped_units > 0) && (
        <Card>
          <div className={`overflow-x-auto ${products.isPlaceholderData ? "opacity-60" : ""}`}>
            <table className="w-full text-[12.5px] tabular-nums">
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th
                      key={c.key + c.label}
                      aria-sort={c.key === sort ? (descending ? "descending" : "ascending") : "none"}
                      title={c.title}
                      className={`p-2 text-[10.5px] font-semibold uppercase tracking-wide text-slate-500 ${c.align === "right" ? "text-right" : "text-left"}`}
                    >
                      <button type="button" onClick={() => toggleSort(c.key)} className="uppercase tracking-wide hover:text-slate-800">
                        {c.label} {c.key === sort ? (descending ? "▾" : "▴") : ""}
                      </button>
                    </th>
                  ))}
                  <th className="p-2 text-left text-[10.5px] font-semibold uppercase tracking-wide text-slate-500">Trend</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <ProductRow
                    key={row.key}
                    row={row}
                    tag={tagFor(row, meanUnits)}
                    expandable={view === "products" && row.product_id !== null}
                    expanded={open === row.product_id}
                    onToggle={() => setOpen((cur) => (cur === row.product_id ? null : row.product_id))}
                    scope={scope}
                    granularity={granularity}
                  />
                ))}
                {lastPage && (
                  <>
                    {data.unmapped_units > 0 && (
                      <tr className="border-t border-slate-100">
                        <td className="p-2">
                          <i>Unmapped SKUs</i> <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">needs mapping</span>
                        </td>
                        <td className="p-2 text-right">{count(data.unmapped_units)}</td>
                        <td className="p-2 text-right text-slate-400">-</td>
                        <td className="p-2 text-right text-slate-400">-</td>
                        <td className="p-2 text-right text-slate-400">-</td>
                        <td className="p-2 text-right text-slate-400">
                          - <PendingBadge />
                        </td>
                        <td colSpan={4} />
                      </tr>
                    )}
                    {view === "products" && (
                      <tr className="border-t border-slate-200 bg-slate-100 font-semibold">
                        <td className="p-2">Total (matches Overview)</td>
                        <td className="p-2 text-right">{count(data.totals.units)}</td>
                        <td className="p-2 text-right" />
                        <td className="p-2 text-right">{gbp(data.totals.revenue)}</td>
                        <td className="p-2 text-right">{gbp((num(data.totals.materials_cogs) ?? 0) + (num(data.totals.kitting_cogs) ?? 0))}</td>
                        <td className="p-2 text-right">{gbp(data.totals.net_profit)}</td>
                        <td className="p-2 text-right">{pct(data.totals.margin_percent)}</td>
                        <td className="p-2 text-right">{data.totals.units ? gbp((num(data.totals.net_profit) ?? 0) / data.totals.units) : "-"}</td>
                        <td className="p-2 text-right">100%</td>
                        <td />
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-3.5 py-2.5 text-xs text-slate-500">
            <span>
              {data.total_rows > 0 ? `Showing ${first}–${last} of ${data.total_rows}` : "No matches"} · page {Math.min(page, pages)} of {pages}
            </span>
            <nav aria-label="Pagination" className="flex items-center gap-1">
              <PageButton disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                ‹ Prev
              </PageButton>
              {pageWindow(page, pages).map((p) => (
                <PageButton key={p} active={p === page} onClick={() => setPage(p)}>
                  {p}
                </PageButton>
              ))}
              <PageButton disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
                Next ›
              </PageButton>
            </nav>
          </div>
          <p className="px-3.5 pb-3 text-[11.5px] text-slate-400">
            Tags: <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${TAG_CLASSES.amber}`}>High volume, low margin</span>{" "}
            <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${TAG_CLASSES.blue}`}>Low volume, high margin</span>{" "}
            <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${TAG_CLASSES.warm}`}>Loss-making</span>
          </p>
        </Card>
      )}

      {view === "products" && scatter.data && scatter.data.rows.some((r) => r.margin_percent !== null) && (
        <>
          <SectionHead title="Price vs margin" sub="Units sold against margin, bubble size is net profit" />
          <Card className="p-3">
            <Scatter
              ariaLabel="Products by units sold and net margin"
              xLabel="Units sold"
              yLabel="Net margin %"
              yFormat={(v) => `${Math.round(v)}%`}
              points={scatter.data.rows
                .filter((r) => r.margin_percent !== null)
                .map((r) => ({
                  key: r.key,
                  label: r.name,
                  x: r.units,
                  y: num(r.margin_percent) as number,
                  size: Math.max(num(r.net_profit) ?? 0, 0),
                  title: `${r.name}: ${r.units} units, ${pct(r.margin_percent)} margin, ${gbp(r.net_profit)} profit`,
                }))}
            />
            {scatter.data.total_rows > scatter.data.rows.length && (
              <p className="px-1 text-[11.5px] text-slate-400">Showing the top {scatter.data.rows.length} of {scatter.data.total_rows} products by profit.</p>
            )}
          </Card>
        </>
      )}
    </>
  );
}

/** Up to five page numbers around the current one. */
export function pageWindow(page: number, pages: number): number[] {
  const size = Math.min(5, pages);
  const startAt = Math.max(1, Math.min(page - 2, pages - size + 1));
  return Array.from({ length: size }, (_, i) => startAt + i);
}

function PageButton({ children, onClick, disabled, active }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; active?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-current={active ? "page" : undefined}
      className={`rounded px-2.5 py-1 font-medium ${active ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}
    >
      {children}
    </button>
  );
}

function ProductRow({
  row,
  tag,
  expandable,
  expanded,
  onToggle,
  scope,
  granularity,
}: {
  row: InsightsProductRow;
  tag: ReturnType<typeof tagFor>;
  expandable: boolean;
  expanded: boolean;
  onToggle: () => void;
  scope: InsightsScope;
  granularity: InsightsGranularity;
}) {
  const failRate = num(row.fail_rate_percent);
  return (
    <Fragment>
      <tr className={`border-t border-slate-100 ${expandable ? "cursor-pointer hover:bg-slate-50" : ""}`} onClick={expandable ? onToggle : undefined}>
        <td className="max-w-[320px] p-2">
          <div className="flex items-center gap-1.5">
            {expandable ? (
              <button type="button" aria-expanded={expanded} aria-label={`${expanded ? "Collapse" : "Expand"} ${row.name}`} onClick={(e) => { e.stopPropagation(); onToggle(); }} className="w-3.5 text-slate-400">
                {expanded ? "▾" : "▸"}
              </button>
            ) : (
              <span className="w-3.5" />
            )}
            <span className="truncate font-semibold" title={row.name}>
              {row.name}
            </span>
            {tag && <span className={`flex-none rounded px-1.5 py-0.5 text-[11px] font-semibold ${TAG_CLASSES[tag.tone]}`}>{tag.text}</span>}
          </div>
          {row.sub && <div className="ml-5 truncate text-[11px] text-slate-400" title={row.sub}>{row.sub}</div>}
        </td>
        <td className="p-2 text-right">{count(row.units)}</td>
        <td className="p-2 text-right" title={failRate === null ? "Never built, or no builds recorded" : undefined}>
          {failRate === null ? <span className="text-slate-300">-</span> : <span className={failRate >= 8 ? "rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800" : ""}>{pct(failRate)}</span>}
        </td>
        <td className="p-2 text-right">{gbp(row.revenue)}</td>
        <td className="p-2 text-right">{gbp(row.cogs)}</td>
        <td className="p-2 text-right" title={num(row.failed_build_cost) ? `After ${gbp(row.failed_build_cost)} of failed builds (${gbp(row.net_profit_before_failed_builds)} before)` : undefined}>
          {gbp(row.net_profit)}
        </td>
        <td className="p-2 text-right">{pct(row.margin_percent)}</td>
        <td className="p-2 text-right">{gbp(row.profit_per_unit)}</td>
        <td className="p-2 text-right">{pct(row.share_of_profit_percent)}</td>
        <td className="p-2">
          <Sparkline values={row.trend.map((v) => num(v) ?? 0)} />
        </td>
      </tr>
      {expanded && <VariantRows productId={row.product_id as number} scope={scope} granularity={granularity} />}
    </Fragment>
  );
}

function VariantRows({ productId, scope, granularity }: { productId: number; scope: InsightsScope; granularity: InsightsGranularity }) {
  const request = { view: "variants" as const, q: "", sort: "net_profit" as const, descending: true, page: 1, pageSize: 50, granularity, productId };
  const variants = useQuery({ queryKey: ["insights", "variants", scope, request], queryFn: () => insightsApi.products(scope, request) });

  if (variants.isLoading) {
    return (
      <tr className="bg-slate-50">
        <td colSpan={10} className="p-2 pl-9 text-xs text-slate-400">
          Loading variants…
        </td>
      </tr>
    );
  }
  if (variants.error) {
    return (
      <tr className="bg-slate-50">
        <td colSpan={10} className="p-2 pl-9">
          <ErrorBanner error={variants.error} />
        </td>
      </tr>
    );
  }
  const rows = variants.data?.rows ?? [];
  if (rows.length === 0) {
    return (
      <tr className="bg-slate-50">
        <td colSpan={10} className="p-2 pl-9 text-xs text-slate-400">
          No variants sold in this period.
        </td>
      </tr>
    );
  }
  return (
    <>
      {rows.map((v) => (
        <tr key={v.key} className="bg-slate-50 text-slate-700">
          <td className="p-2 pl-9">
            {v.name} <span className="text-[11px] text-slate-400">{v.sub}</span>
          </td>
          <td className="p-2 text-right">{count(v.units)}</td>
          <td className="p-2 text-right">{v.fail_rate_percent === null ? <span className="text-slate-300">-</span> : pct(v.fail_rate_percent)}</td>
          <td className="p-2 text-right">{gbp(v.revenue)}</td>
          <td className="p-2 text-right">{gbp(v.cogs)}</td>
          <td className="p-2 text-right">{gbp(v.net_profit)}</td>
          <td className="p-2 text-right">{pct(v.margin_percent)}</td>
          <td className="p-2 text-right">{gbp(v.profit_per_unit)}</td>
          <td className="p-2 text-right">{pct(v.share_of_profit_percent)}</td>
          <td className="p-2 text-[11px] text-slate-400">
            {Object.entries(v.channel_units)
              .map(([channel, units]) => `${CHANNEL_LABELS[channel as keyof typeof CHANNEL_LABELS] ?? channel} ${units}`)
              .join(" · ")}
          </td>
        </tr>
      ))}
    </>
  );
}
