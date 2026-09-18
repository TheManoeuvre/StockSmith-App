import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useState, type MouseEvent } from "react";
import { ordersApi } from "../../api/orders";
import type { Order, OrderStatus } from "../../api/types";
import { CopyButton } from "../../components/common/CopyButton";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { FilterTabs } from "../../components/common/FilterTabs";
import { Th } from "../../components/common/ListTable";
import { useRefreshOnSync } from "../../hooks/useRefreshOnSync";
import { formatMoney } from "../../lib/money";
import { formatDayMonth } from "../../lib/format";
import { maskBuyerName } from "../../lib/names";
import { orderFulfilment } from "../../lib/orderFulfilment";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../../lib/platforms";

/**
 * Pathless layout for /orders: the list lives here (not in index.tsx) so it stays mounted
 * and visible while `$orderId`/`new` render into the `<Outlet>` as a slide-over panel on top
 * of it — see components/common/DetailPanel.tsx. index.tsx is a trivial route now; this
 * component is what actually renders the list.
 */
export const Route = createFileRoute("/orders")({
  component: OrdersLayout,
});

export const ORDERS_PAGE_SIZE = 50;

type OrderTab = "awaiting" | "shipped" | "cancelled";

const STATUS_TABS: { id: OrderTab; label: string }[] = [
  { id: "awaiting", label: "Awaiting Shipment" },
  { id: "shipped", label: "Shipped" },
  { id: "cancelled", label: "Cancelled" },
];

export const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "Pending",
  allocated: "Allocated",
  shipped: "Shipped",
  cancelled: "Cancelled",
};

export const STATUS_CLASSES: Record<OrderStatus, string> = {
  pending: "bg-amber-100 text-amber-800",
  allocated: "bg-blue-100 text-blue-800",
  shipped: "bg-green-100 text-green-800",
  cancelled: "bg-slate-200 text-slate-600",
};

/** A terminal order has nothing left to fulfil — it drops out of "Awaiting shipment". */
function isDone(o: Order): boolean {
  return o.status === "shipped" || o.status === "cancelled";
}

/** Margin as a % of order value, or null when either figure is missing. */
function orderMarginPct(order: Order): number | null {
  const value = order.grand_total ?? order.subtotal;
  if (value == null || order.net_profit == null) return null;
  const v = Number(value);
  return v > 0 ? (Number(order.net_profit) / v) * 100 : null;
}

// The marketplace's ship-by deadline, or "—" when it didn't report one (manual orders,
// and any synced order predating this field). A done order shows the date plain — the
// deadline no longer matters once it's shipped or cancelled.
function dueLabel(order: Order): string {
  if (!order.ship_by_date) return "—";
  return formatDayMonth(order.ship_by_date);
}

// Red once the deadline has passed on an order still awaiting shipment — the one state
// that actually needs chasing. Shipped/cancelled orders never render as overdue.
function dueTone(order: Order): string {
  if (isDone(order) || !order.ship_by_date) return "text-slate-500";
  return new Date(order.ship_by_date).getTime() < Date.now()
    ? "font-semibold text-red-600"
    : "text-slate-500";
}

function netProfitSub(order: Order): string {
  if (order.cogs_pending) return "COGS pending";
  if (order.postage_cost_missing) return "No postage cost";
  if (order.platform != null && order.payment_fees == null) return "Fees not reported";
  const pct = orderMarginPct(order);
  return pct == null ? "" : `${pct.toFixed(0)}% margin`;
}

function OrdersLayout() {
  // In the layout rather than the list, so a background import still refreshes the list
  // (and the open order's siblings) while the slide-over is up.
  useRefreshOnSync();
  return (
    <>
      <OrdersListContent />
      <Outlet />
    </>
  );
}

function OrdersListContent() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<OrderTab>("awaiting");
  const [page, setPage] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");

  // Debounce the search box into the query key so a keystroke isn't a request, same as the
  // products list. A change to the effective term sends us back to page 0 — a later page of
  // the wider list is usually past the end of the narrower one.
  useEffect(() => {
    const trimmed = searchInput.trim();
    const t = setTimeout(() => {
      setQ(trimmed);
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["orders", tab, page, q],
    queryFn: () => ordersApi.list(ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE, tab, q),
    placeholderData: keepPreviousData,
  });

  // Cheap per-tab totals for the tab strip and subtitle — kept off the ["orders", …] key so
  // useSiblingNav in the slide-over doesn't mistake a single-row count page for the sibling
  // sequence.
  const countQueries = useQueries({
    queries: STATUS_TABS.map(({ id }) => ({
      queryKey: ["order-counts", id],
      queryFn: () => ordersApi.list(1, 0, id).then((p) => p.total),
    })),
  });
  const [awaitingCount, shippedCount, cancelledCount] = countQueries.map(
    (q) => q.data,
  );
  const countFor = (id: OrderTab) =>
    ({ awaiting: awaitingCount, shipped: shippedCount, cancelled: cancelledCount })[id];
  const total = data?.total ?? 0;

  const actionMutation = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: "allocate" | "ship" }) =>
      kind === "ship" ? ordersApi.ship(id) : ordersApi.allocate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["order-counts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  const changeTab = (id: string) => {
    setTab(id as OrderTab);
    setPage(0);
  };

  if (isLoading) return <p>Loading orders…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  // The backend already returns rows in the right order for the active tab — soonest-due
  // first for "awaiting" (see list_orders), newest-placed first for "shipped"/"cancelled" —
  // so there's nothing left to re-sort client-side.
  const rows = data?.items ?? [];

  const renderRow = (order: Order) => (
    <OrderRow
      key={order.id}
      order={order}
      onOpen={() =>
        navigate({
          to: "/orders/$orderId",
          params: { orderId: String(order.id) },
        })
      }
      onAction={(kind) => actionMutation.mutate({ id: order.id, kind })}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Orders</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {awaitingCount ?? "…"} awaiting shipment · {shippedCount ?? "…"}{" "}
            shipped
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CsvImportExport
            onExport={ordersApi.exportCsv}
            invalidateKey={["orders"]}
          />
          <Link
            to="/orders/new"
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
          >
            New order
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <FilterTabs
          tabs={STATUS_TABS.map((t) => ({ id: t.id, label: t.label, count: countFor(t.id) }))}
          active={tab}
          onChange={changeTab}
        />
        <input
          className="w-64 rounded border border-slate-300 px-2.5 py-1.5 text-sm"
          placeholder="Search order no., item, SKU, notes…"
          aria-label="Search orders"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>Order</Th>
            <Th>Channel</Th>
            <Th>Due</Th>
            <Th>Items</Th>
            <Th>Fulfilment</Th>
            <Th align="right">Value</Th>
            <Th align="right">Net profit</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={8} className="p-6 text-center text-slate-500">
                {q ? `No orders match "${q}"` : "No orders"}
              </td>
            </tr>
          ) : (
            rows.map(renderRow)
          )}
        </tbody>
      </table>

      <div className="flex items-center justify-between text-sm text-slate-500">
        <span>
          {total === 0
            ? "No orders"
            : `Showing ${page * ORDERS_PAGE_SIZE + 1}–${Math.min(page * ORDERS_PAGE_SIZE + rows.length, total)} of ${total}`}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="rounded border border-slate-300 px-3 py-1.5 disabled:opacity-40"
          >
            Prev
          </button>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(page + 1) * ORDERS_PAGE_SIZE >= total}
            className="rounded border border-slate-300 px-3 py-1.5 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

function OrderRow({
  order,
  onOpen,
  onAction,
}: {
  order: Order;
  onOpen: () => void;
  onAction: (kind: "allocate" | "ship") => void;
}) {
  const fulfilment = orderFulfilment(order);
  const discount =
    order.discount_amount != null ? Number(order.discount_amount) : 0;
  const listPrice =
    discount > 0 && order.subtotal != null ? Number(order.subtotal) + discount : null;
  const marginPct = orderMarginPct(order);
  const netFg =
    order.net_profit != null && Number(order.net_profit) < 0
      ? "text-red-600"
      : marginPct != null && marginPct < 15
        ? "text-amber-700"
        : "";

  // Clicking anywhere on the row opens the slide-over; the action button (and any future
  // control) handles its own click.
  const openDetail = (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest("a, button, input, select, label")) return;
    onOpen();
  };

  return (
    <tr
      onClick={openDetail}
      className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${order.status === "cancelled" ? "opacity-60" : ""}`}
    >
      <td className="p-2 align-top">
        <div className="font-medium">
          {order.external_order_id ? `#${order.external_order_id}` : `#${order.id}`}
        </div>
        <div className="text-[11px] text-slate-400">
          {maskBuyerName(order.buyer_name) ?? "—"}
        </div>
      </td>
      <td className="p-2 align-top">
        <span
          className={`rounded px-1.5 py-0.5 text-[10.5px] font-semibold ${
            order.platform
              ? PLATFORM_COLORS[order.platform].muted
              : "border border-slate-200 bg-slate-50 text-slate-600"
          }`}
        >
          {order.platform ? PLATFORM_LABELS[order.platform] : "Manual"}
        </span>
      </td>
      <td className={`p-2 align-top ${dueTone(order)}`}>{dueLabel(order)}</td>
      <td className="p-2 align-top">
        {order.lines
          .filter((l) => l.ordered_qty > 0)
          .map((l) => (
            <div key={l.id} className="flex items-center gap-1.5 leading-tight">
              <span className="font-semibold tabular-nums text-slate-600">
                {l.ordered_qty}×
              </span>
              <span>
                {l.needs_mapping
                  ? `Unmapped: ${l.sku ?? "—"}`
                  : (l.product_name ?? "—")}
              </span>
            </div>
          ))}
      </td>
      <td className="p-2 align-top">
        <span
          className={`inline-flex items-center gap-1.5 font-semibold ${fulfilment.toneClass}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          {fulfilment.label}
        </span>
        {fulfilment.detail && (
          <div className="flex items-center gap-1 text-[11px] text-slate-500">
            <span>{fulfilment.detail}</span>
            {fulfilment.trackingNumber && (
              <span onClick={(e) => e.stopPropagation()}>
                <CopyButton value={fulfilment.trackingNumber} label="Copy tracking number" />
              </span>
            )}
          </div>
        )}
      </td>
      <td className="p-2 text-right align-top tabular-nums">
        {formatMoney(order.grand_total ?? order.subtotal, order.currency)}
        {listPrice != null && (
          <div className="text-[10.5px] text-slate-400">
            {formatMoney(String(listPrice), order.currency)} −{" "}
            {formatMoney(order.discount_amount, order.currency)} disc
          </div>
        )}
      </td>
      <td className={`p-2 text-right align-top font-semibold tabular-nums ${netFg}`}>
        {formatMoney(order.net_profit, order.currency)}
        {netProfitSub(order) && (
          <div className="text-[10.5px] font-normal text-slate-400">
            {netProfitSub(order)}
          </div>
        )}
      </td>
      <td className="p-2 text-right align-top">
        {fulfilment.action && (
          <button
            onClick={() => {
              if (fulfilment.action!.kind === "open") onOpen();
              else onAction(fulfilment.action!.kind);
            }}
            className="rounded border border-slate-300 px-2.5 py-1 text-[11.5px] font-semibold hover:bg-slate-50"
          >
            {fulfilment.action.label}
          </button>
        )}
      </td>
    </tr>
  );
}
