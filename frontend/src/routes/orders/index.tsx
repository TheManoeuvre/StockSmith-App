import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { ordersApi } from "../../api/orders";
import type { Order, OrderStatus } from "../../api/types";
import { formatMoney } from "../../lib/money";
import { maskBuyerName } from "../../lib/names";
import { PLATFORM_LABELS } from "../../lib/platforms";

export const Route = createFileRoute("/orders/")({
  component: OrdersList,
});

const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "Pending",
  allocated: "Allocated",
  shipped: "Shipped",
  cancelled: "Cancelled",
};

const STATUS_CLASSES: Record<OrderStatus, string> = {
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

function OrdersList() {
  const { data, isLoading, error } = useQuery({ queryKey: ["orders"], queryFn: () => ordersApi.list() });

  if (isLoading) return <p>Loading orders…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Orders</h1>
        <Link to="/orders/new" className="rounded bg-slate-900 px-4 py-2 text-white">
          New order
        </Link>
      </div>

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Order date</th>
            <th className="p-2">Order</th>
            <th className="p-2">Allocated / ordered</th>
            <th className="p-2">Order Value</th>
            <th className="p-2">Net Profit</th>
            <th className="p-2">Platform</th>
            <th className="p-2">Status</th>
            <th className="p-2"></th>
          </tr>
        </thead>
        <tbody>
          {data?.map((order) => (
            <tr key={order.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="p-2">{new Date(order.order_placed_at).toLocaleDateString()}</td>
              <td className="p-2">
                <Link to="/orders/$orderId" params={{ orderId: String(order.id) }} className="text-slate-900 underline">
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
              <td className="p-2">{formatMoney(order.grand_total, order.currency)}</td>
              <td className={`p-2 ${order.net_profit != null && Number(order.net_profit) < 0 ? "text-red-600" : ""}`}>
                {formatMoney(order.net_profit, order.currency)}
              </td>
              <td className="p-2">{order.platform ? PLATFORM_LABELS[order.platform] : "Manual"}</td>
              <td className="p-2">
                <span className={`rounded px-2 py-0.5 text-xs ${STATUS_CLASSES[order.status]}`}>
                  {STATUS_LABELS[order.status]}
                </span>
              </td>
              <td className="p-2">
                {order.lines.some((l) => l.needs_mapping) && (
                  <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Needs mapping</span>
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
    </div>
  );
}
