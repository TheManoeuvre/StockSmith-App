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
import { useState, type MouseEvent } from "react";
import { ordersApi } from "../../api/orders";
import type { Order, OrderStatus } from "../../api/types";
import { Th } from "../../components/common/ListTable";
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

function netProfitSub(order: Order): string {
  if (order.cogs_pending) return "COGS pending";
  if (order.postage_cost_missing) return "No postage cost";
  if (order.platform != null && order.payment_fees == null) return "Fees not reported";
  const pct = orderMarginPct(order);
  return pct == null ? "" : `${pct.toFixed(0)}% margin`;
}

function OrdersLayout() {
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
  const [page, setPage] = useState(0);

  const { data, isLoading, error } = useQuery({
    queryKey: ["orders", page],
    queryFn: () => ordersApi.list(ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });

  // Cheap totals for the subtitle — kept off the ["orders", …] key so useSiblingNav in the
  // slide-over doesn't mistake a single-row count page for the sibling sequence.
  const countQueries = useQueries({
    queries: (["all", "shipped", "cancelled"] as const).map((id) => ({
      queryKey: ["order-counts", id],
      queryFn: () =>
        ordersApi
          .list(1, 0, id === "all" ? undefined : id)
          .then((p) => p.total),
    })),
  });
  const [allCount, shippedCount, cancelledCount] = countQueries.map(
    (q) => q.data,
  );
  const total = data?.total ?? 0;
  const awaitingCount =
    allCount != null && shippedCount != null && cancelledCount != null
      ? allCount - shippedCount - cancelledCount
      : null;

  const actionMutation = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: "allocate" | "ship" }) =>
      kind === "ship" ? ordersApi.ship(id) : ordersApi.allocate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["order-counts"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  if (isLoading) return <p>Loading orders…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  const items = data?.items ?? [];
  const placedTs = (o: Order) => new Date(o.order_placed_at).getTime();
  // Anything still to fulfil is pinned above, oldest first — a stale order is the one that
  // needs chasing. Shipped and cancelled fall to a second group, newest first. The oldest-
  // first sort is within the loaded page; a true cross-page pin needs a backend sort param.
  const awaiting = items
    .filter((o) => !isDone(o))
    .sort((a, b) => placedTs(a) - placedTs(b));
  const done = items
    .filter(isDone)
    .sort((a, b) => placedTs(b) - placedTs(a));
  const rows = [...awaiting, ...done];

  const groups = [
    { label: "Awaiting shipment", note: "oldest first", rows: awaiting },
    { label: "Shipped & cancelled", note: "newest first", rows: done },
  ].filter((g) => g.rows.length > 0);

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
        <Link
          to="/orders/new"
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
        >
          New order
        </Link>
      </div>

      <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>Order</Th>
            <Th>Channel</Th>
            <Th>Placed</Th>
            <Th>Items</Th>
            <Th>Fulfilment</Th>
            <Th align="right">Value</Th>
            <Th align="right">Net profit</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        {groups.length === 0 ? (
          <tbody>
            <tr>
              <td colSpan={8} className="p-6 text-center text-slate-500">
                No orders
              </td>
            </tr>
          </tbody>
        ) : (
          groups.map((group) => (
            <tbody key={group.label}>
              <tr className="border-b border-slate-200 bg-slate-100">
                <th
                  colSpan={8}
                  className="p-2 text-left text-[11.5px] font-semibold text-slate-600"
                >
                  {group.label}
                  <span className="ml-2 font-normal text-slate-400">
                    {group.rows.length} · {group.note}
                  </span>
                </th>
              </tr>
              {group.rows.map(renderRow)}
            </tbody>
          ))
        )}
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
      <td className="p-2 align-top text-slate-500">
        {formatDayMonth(order.order_placed_at)}{" "}
        {new Date(order.order_placed_at).toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
        })}
      </td>
      <td className="p-2 align-top">
        {order.lines.map((l) => (
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
          <div className="text-[11px] text-slate-500">{fulfilment.detail}</div>
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
