import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ordersApi } from "../../api/orders";
import type { Order, OrderStatus } from "../../api/types";
import { Badge } from "../../components/common/Badge";
import { Th } from "../../components/common/ListTable";
import { formatMoney } from "../../lib/money";
import { maskBuyerName } from "../../lib/names";
import { PLATFORM_LABELS } from "../../lib/platforms";

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

function lineSummary(order: Order): string {
  const ordered = order.lines.reduce((sum, l) => sum + l.ordered_qty, 0);
  const allocated = order.lines.reduce((sum, l) => sum + l.allocated_qty, 0);
  return `${allocated}/${ordered}`;
}

function orderLabel(order: Order): string {
  const idPart = order.external_order_id ?? `Order #${order.id}`;
  const masked = maskBuyerName(order.buyer_name);
  return masked ? `${idPart} - ${masked}` : idPart;
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
  const [page, setPage] = useState(0);
  const { data, isLoading, error } = useQuery({
    queryKey: ["orders", page],
    queryFn: () => ordersApi.list(ORDERS_PAGE_SIZE, page * ORDERS_PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const orders = data?.items;
  const total = data?.total ?? 0;

  if (isLoading) return <p>Loading orders…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Orders</h1>
        <Link
          to="/orders/new"
          className="rounded bg-slate-900 px-4 py-2 text-white"
        >
          New order
        </Link>
      </div>

      <table className="w-full border-collapse bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <Th>Order date</Th>
            <Th>Order</Th>
            <Th>Allocated / ordered</Th>
            <Th>Order Value</Th>
            <Th>Net Profit</Th>
            <Th>Platform</Th>
            <Th>Status</Th>
            <Th>{""}</Th>
          </tr>
        </thead>
        <tbody>
          {orders?.map((order) => (
            <tr
              key={order.id}
              className="border-b border-slate-100 hover:bg-slate-50"
            >
              <td className="p-2">
                {new Date(order.order_placed_at).toLocaleDateString()}
              </td>
              <td className="p-2">
                <Link
                  to="/orders/$orderId"
                  params={{ orderId: String(order.id) }}
                  className="text-slate-900 underline"
                >
                  {orderLabel(order)}
                </Link>
                {order.sync_issue && (
                  <span
                    className="ml-2 rounded bg-red-100 px-2 py-0.5 text-xs text-red-800"
                    title={order.sync_issue}
                  >
                    Sync issue
                  </span>
                )}
              </td>
              <td className="p-2">{lineSummary(order)}</td>
              <td className="p-2">
                {formatMoney(order.grand_total, order.currency)}
              </td>
              <td
                className={`p-2 ${order.net_profit != null && Number(order.net_profit) < 0 ? "text-red-600" : ""}`}
              >
                {formatMoney(order.net_profit, order.currency)}
                {order.cogs_pending && (
                  <span
                    className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
                    title="One or more lines haven't been allocated yet, so their cost of goods isn't captured — this figure doesn't include it."
                  >
                    COGS pending
                  </span>
                )}
                {order.postage_cost_missing && (
                  <span
                    className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
                    title="This order shipped without a shipping profile, so what the postage cost was never recorded — this figure doesn't deduct it. Assign the product a shipping profile so future orders capture it."
                  >
                    No postage cost
                  </span>
                )}
              </td>
              <td className="p-2">
                {order.platform ? PLATFORM_LABELS[order.platform] : "Manual"}
              </td>
              <td className="p-2">
                <Badge className={STATUS_CLASSES[order.status]}>
                  {STATUS_LABELS[order.status]}
                </Badge>
              </td>
              <td className="p-2">
                {order.lines.some((l) => l.needs_mapping) && (
                  <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                    Needs mapping
                  </span>
                )}
                {order.pending_marketplace_cancellation && (
                  <span
                    className="ml-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
                    title={`${order.platform ? PLATFORM_LABELS[order.platform] : "The marketplace"} reports this cancelled — review needed`}
                  >
                    Cancellation reported
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex items-center justify-between text-sm text-slate-500">
        <span>
          {total === 0
            ? "No orders"
            : `Showing ${page * ORDERS_PAGE_SIZE + 1}–${Math.min(page * ORDERS_PAGE_SIZE + (orders?.length ?? 0), total)} of ${total}`}
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
