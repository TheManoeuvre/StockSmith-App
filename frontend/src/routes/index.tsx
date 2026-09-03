import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dashboardApi } from "../api/dashboard";
import { materialsApi } from "../api/materials";
import type { DashboardSummary, ListingPlatform, LowStockMaterial } from "../api/types";
import { Badge } from "../components/common/Badge";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { GroupHeaderRow, Th } from "../components/common/ListTable";
import { formatDayMonth, roundQty } from "../lib/format";
import { formatMoney } from "../lib/money";
import {
  formatLeadTime,
  formatWeeksShort,
  STOCKOUT_BADGE_CLASS,
  STOCKOUT_LABEL,
} from "../lib/forecast";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../lib/platforms";

function groupBySupplier(
  materials: LowStockMaterial[],
): { supplierName: string; materials: LowStockMaterial[] }[] {
  const groups: { supplierName: string; materials: LowStockMaterial[] }[] = [];
  for (const m of materials) {
    const supplierName = m.supplier_name ?? "No supplier";
    const last = groups[groups.length - 1];
    if (last && last.supplierName === supplierName) {
      last.materials.push(m);
    } else {
      groups.push({ supplierName, materials: [m] });
    }
  }
  return groups;
}

/** One blocked-orders row, whichever array it came from. */
type BlockedRow = {
  key: string;
  orderId: number;
  blockedOn: "Short stock" | "Short packaging";
  item: string;
  shortBy: string;
  placedAt: string;
  platform: ListingPlatform | null;
  build: { productId: number; variantId: number | null } | null;
};

function blockedRows(data: DashboardSummary): BlockedRow[] {
  const rows: BlockedRow[] = [];
  for (const o of data.orders_awaiting_inventory) {
    rows.push({
      key: `inv-${o.line_id}`,
      orderId: o.order_id,
      blockedOn: "Short stock",
      item:
        (o.product_name ?? "—") + (o.variant_name ? ` — ${o.variant_name}` : ""),
      shortBy: String(o.short_by),
      placedAt: o.order_placed_at,
      platform: o.platform,
      build:
        o.product_id != null
          ? { productId: o.product_id, variantId: o.variant_id }
          : null,
    });
  }
  for (const [i, o] of data.orders_awaiting_packaging.entries()) {
    rows.push({
      key: `pkg-${o.order_id}-${o.material_id}-${i}`,
      orderId: o.order_id,
      blockedOn: "Short packaging",
      item: o.material_name,
      shortBy: roundQty(o.short_by),
      placedAt: o.order_placed_at,
      platform: o.platform,
      build: null,
    });
  }
  rows.sort((a, b) => a.placedAt.localeCompare(b.placedAt));
  return rows;
}

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: dashboardApi.summary,
  });

  const draftPurchaseMutation = useMutation({
    mutationFn: (materialId: number) =>
      materialsApi.createDraftPurchase(materialId),
    onSuccess: (purchase) => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      navigate({
        to: "/purchases/$purchaseId",
        params: { purchaseId: String(purchase.id) },
      });
    },
  });

  if (isLoading) return <p>Loading dashboard…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;
  if (!data) return null;

  const awaitingInventory = data.orders_awaiting_inventory.length;
  const awaitingPackaging = data.orders_awaiting_packaging.length;
  const blockedCount = awaitingInventory + awaitingPackaging;
  const rows = blockedRows(data);

  const criticalCount = data.low_stock_materials.filter(
    (m) => m.status === "critical",
  ).length;
  const warningCount = data.low_stock_materials.filter(
    (m) => m.status === "warning",
  ).length;

  const dueByClass = { A: 0, B: 0, C: 0 };
  for (const item of data.items_due_for_count) dueByClass[item.abc_class] += 1;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Link
          to="/orders"
          className="block rounded-lg transition hover:shadow-md"
        >
          <KpiCard
            label="Blocked orders"
            value={String(blockedCount)}
            unit="right now"
            note={`${awaitingInventory} short on stock · ${awaitingPackaging} short on packaging`}
            accent={
              blockedCount > 0 ? "border-l-red-500" : "border-l-slate-400"
            }
          />
        </Link>
        <Link
          to="/materials"
          className="block rounded-lg transition hover:shadow-md"
        >
          <KpiCard
            label="Materials at risk"
            value={String(data.low_stock_materials.length)}
            unit="of tracked"
            note={
              data.low_stock_materials.length > 0
                ? `${criticalCount} critical · ${warningCount} warning`
                : "all above threshold"
            }
            accent={
              data.low_stock_materials.length > 0
                ? "border-l-amber-500"
                : "border-l-slate-400"
            }
          />
        </Link>
        <Link
          to="/stock-takes"
          search={{ overdue: true }}
          className="block rounded-lg transition hover:shadow-md"
        >
          <KpiCard
            label="Overdue counts"
            value={String(data.items_due_for_count_total)}
            unit="items"
            note={
              data.items_due_for_count_total > 0
                ? `A ${dueByClass.A} · B ${dueByClass.B} · C ${dueByClass.C}`
                : "nothing overdue"
            }
            accent={
              data.items_due_for_count_total > 0
                ? "border-l-slate-500"
                : "border-l-slate-400"
            }
          />
        </Link>
        <Link
          to="/materials"
          className="block rounded-lg transition hover:shadow-md"
        >
          <KpiCard
            label="Inventory value"
            value={formatMoney(data.total_inventory_value, "GBP")}
            unit="on hand"
            note={`${data.active_product_count} active products`}
            accent="border-l-blue-600"
          />
        </Link>
      </div>

      <Card
        title="Blocked orders"
        action={
          <Link to="/orders" className="text-xs text-blue-700 hover:underline">
            Open Orders →
          </Link>
        }
      >
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">
            No blocked orders — everything on order can be fulfilled from stock.
          </p>
        ) : (
          <table className="w-full border-collapse text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-slate-200">
                <Th>Order</Th>
                <Th>Channel</Th>
                <Th>Blocked on</Th>
                <Th>Item</Th>
                <Th align="right">Short by</Th>
                <Th>Placed</Th>
                <Th>{""}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-b border-slate-100">
                  <td className="p-2">
                    <Link
                      to="/orders/$orderId"
                      params={{ orderId: String(r.orderId) }}
                      className="text-slate-900 underline"
                    >
                      #{r.orderId}
                    </Link>
                  </td>
                  <td className="p-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold ${
                        r.platform
                          ? PLATFORM_COLORS[r.platform].muted
                          : "border border-slate-200 bg-slate-50 text-slate-600"
                      }`}
                    >
                      {r.platform ? PLATFORM_LABELS[r.platform] : "Manual"}
                    </span>
                  </td>
                  <td className="p-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          r.blockedOn === "Short stock"
                            ? "bg-red-500"
                            : "bg-amber-500"
                        }`}
                      />
                      {r.blockedOn}
                    </span>
                  </td>
                  <td className="p-2">{r.item}</td>
                  <td className="p-2 text-right text-red-600 tabular-nums">
                    {r.shortBy}
                  </td>
                  <td className="p-2">{formatDayMonth(r.placedAt)}</td>
                  <td className="p-2">
                    {r.build && (
                      <Link
                        to="/products/$productId"
                        params={{ productId: String(r.build.productId) }}
                        // Straight to the build form with the variant already chosen, rather
                        // than dropping the user on Details to find the Stock tab and
                        // re-pick the variant they were just looking at here.
                        search={{
                          tab: "stock",
                          ...(r.build.variantId != null
                            ? { variantId: r.build.variantId }
                            : {}),
                        }}
                        className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                      >
                        Build now
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card
          title="Time to stockout"
          hint="Weeks until you can't fulfil demand — grouped by supplier, finished-goods cover included."
        >
          {data.low_stock_materials.length === 0 ? (
            <p className="text-sm text-slate-500">
              Nothing below its warning threshold.
            </p>
          ) : (
            <table className="w-full border-collapse text-left text-[12.5px]">
              <thead>
                <tr className="border-b border-slate-200">
                  <Th>Material</Th>
                  <Th>Status</Th>
                  <Th align="right">To stockout</Th>
                  <Th align="right">Rate</Th>
                  <Th align="right">On hand</Th>
                  <Th align="right">On order</Th>
                  <Th>{""}</Th>
                </tr>
              </thead>
              {groupBySupplier(data.low_stock_materials).map((group) => {
                const lead = formatLeadTime(group.materials[0]?.lead_time_weeks);
                return (
                <tbody key={group.supplierName}>
                  <GroupHeaderRow
                    label={lead ? `${group.supplierName} · ${lead}` : group.supplierName}
                    count={group.materials.length}
                    colSpan={7}
                  />
                  {group.materials.map((m) => (
                    <tr key={m.id} className="border-b border-slate-100">
                      <td className="p-2">{m.name}</td>
                      <td className="p-2">
                        <Badge className={STOCKOUT_BADGE_CLASS[m.status]}>
                          {STOCKOUT_LABEL[m.status]}
                        </Badge>
                      </td>
                      <td className="p-2 text-right tabular-nums">
                        {formatWeeksShort(m.weeks_of_supply)}
                      </td>
                      <td className="p-2 text-right tabular-nums">
                        {m.consumption_rate_per_week != null
                          ? `${roundQty(m.consumption_rate_per_week)}/wk`
                          : "—"}
                      </td>
                      <td className="p-2 text-right text-red-600 tabular-nums">
                        {roundQty(m.current_qty)}
                      </td>
                      <td className="p-2 text-right tabular-nums">
                        {Number(m.on_order_qty) > 0
                          ? roundQty(m.on_order_qty)
                          : "—"}
                      </td>
                      <td className="p-2">
                        <button
                          onClick={() => draftPurchaseMutation.mutate(m.id)}
                          className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                        >
                          Create draft purchase
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
                );
              })}
            </table>
          )}
          <ErrorBanner error={draftPurchaseMutation.error} />
        </Card>

        <div className="flex flex-col gap-6">
          <StockTakeCard data={data} />
          <MarginMovedCard data={data} />
        </div>
      </div>
    </div>
  );
}

function StockTakeCard({ data }: { data: DashboardSummary }) {
  const take = data.open_stock_take;
  const due = data.items_due_for_count;
  if (!take && data.unresolved_variance_count === 0 && due.length === 0) {
    return null;
  }

  const pct =
    take && take.line_count > 0
      ? Math.round((take.counted_count / take.line_count) * 100)
      : 0;

  return (
    <Card title="Stock take">
      {take ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium text-slate-900">Count in progress</span>
            <Link
              to="/stock-takes/$stockTakeId"
              params={{ stockTakeId: String(take.id) }}
              className="text-xs text-blue-700 hover:underline"
            >
              Open →
            </Link>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-blue-600"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-xs text-slate-500">
            {take.counted_count} / {take.line_count} counted · open{" "}
            {take.open_days} day{take.open_days === 1 ? "" : "s"}
          </p>
        </div>
      ) : (
        <p className="text-sm text-slate-500">No stock take in progress.</p>
      )}

      <p className="mt-3 text-xs">
        {data.unresolved_variance_count > 0 ? (
          <Link
            to="/stock-takes/unresolved"
            className="text-amber-800 hover:underline"
          >
            {data.unresolved_variance_count} unresolved variance
            {data.unresolved_variance_count === 1 ? "" : "s"} waiting on a
            decision
          </Link>
        ) : (
          <span className="text-slate-400">Nothing escalated</span>
        )}
      </p>

      {due.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Due for counting
          </p>
          <ul className="flex flex-col gap-1 text-[12.5px]">
            {due.slice(0, 5).map((item) => (
              <li
                key={`${item.scope}-${item.material_id ?? item.product_id}-${item.variant_id ?? "base"}`}
                className="flex items-center gap-2"
              >
                <Badge className="bg-slate-100 text-slate-600">
                  {item.abc_class}
                </Badge>
                {item.scope === "material" ? (
                  <Link
                    to="/materials/$materialId"
                    params={{ materialId: String(item.material_id) }}
                    className="flex-1 truncate text-slate-900 hover:underline"
                  >
                    {item.name}
                  </Link>
                ) : (
                  <Link
                    to="/products/$productId"
                    params={{ productId: String(item.product_id) }}
                    className="flex-1 truncate text-slate-900 hover:underline"
                  >
                    {item.name}
                  </Link>
                )}
                <span className="shrink-0 text-xs text-slate-500">
                  {item.days_overdue === null
                    ? "Never counted"
                    : item.days_overdue === 0
                      ? "Due today"
                      : `${item.days_overdue} days over`}
                </span>
              </li>
            ))}
          </ul>
          {data.items_due_for_count_total > due.slice(0, 5).length && (
            <p className="mt-1 text-xs text-slate-400">
              Showing {Math.min(5, due.length)} of{" "}
              {data.items_due_for_count_total}
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

function MarginMovedCard({ data }: { data: DashboardSummary }) {
  if (data.margin_alerts.length === 0) return null;
  const top = [...data.margin_alerts]
    .sort(
      (a, b) =>
        Math.abs(
          Number(b.current_margin_percent) - Number(b.previous_margin_percent),
        ) -
        Math.abs(
          Number(a.current_margin_percent) - Number(a.previous_margin_percent),
        ),
    )
    .slice(0, 3);

  return (
    <Card title="Margin moved" hint="Largest swings since the last snapshot.">
      <ul className="flex flex-col gap-2 text-[12.5px]">
        {top.map((a) => {
          const prev = Number(a.previous_margin_percent);
          const cur = Number(a.current_margin_percent);
          const diff = cur - prev;
          return (
            <li key={a.product_id} className="flex items-center justify-between gap-2">
              <Link
                to="/products/$productId"
                params={{ productId: String(a.product_id) }}
                search={{ tab: "pricing" }}
                className="flex-1 truncate text-slate-900 hover:underline"
              >
                {a.name}
              </Link>
              <span
                className={
                  diff < 0 ? "text-red-600 tabular-nums" : "text-green-700 tabular-nums"
                }
              >
                {prev.toFixed(1)}% → {cur.toFixed(1)}% ({diff > 0 ? "+" : ""}
                {diff.toFixed(1)} pts)
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Card({
  title,
  hint,
  action,
  children,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {hint && <p className="text-xs text-slate-500">{hint}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function KpiCard({
  label,
  value,
  unit,
  note,
  accent,
}: {
  label: string;
  value: string;
  unit?: string;
  note?: string;
  accent: string;
}) {
  return (
    <div
      className={`h-full rounded-lg border-l-[3px] bg-white p-4 shadow-sm ${accent}`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="flex items-baseline gap-1.5">
        <span className="text-[28px] font-semibold leading-tight tracking-tight">
          {value}
        </span>
        {unit && <span className="text-xs text-slate-400">{unit}</span>}
      </p>
      {note && (
        <p className="mt-1 text-xs leading-snug text-slate-500">{note}</p>
      )}
    </div>
  );
}
