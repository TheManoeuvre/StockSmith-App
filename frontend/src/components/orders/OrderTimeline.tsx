import type { Order } from "../../api/types";
import { Badge } from "../common/Badge";
import { formatDayMonth } from "../../lib/format";

/**
 * Synthesised from the timestamps the order already carries — there's no order-events log,
 * so "reserved" / "paid" are approximate (dated to the order or the last financial sync).
 */
export function OrderTimeline({ order }: { order: Order }) {
  const events: { at: string; label: string; badge: string; badgeClass: string }[] = [];

  events.push({
    at: order.order_placed_at,
    label: "Order placed",
    badge: "Placed",
    badgeClass: "bg-slate-100 text-slate-600",
  });
  if (order.payment_status) {
    events.push({
      at: order.financials_synced_at ?? order.order_placed_at,
      label: "Payment confirmed",
      badge: "Paid",
      badgeClass: "bg-slate-100 text-slate-600",
    });
  }
  if (order.lines.some((l) => l.allocated_qty > 0)) {
    events.push({
      at: order.order_placed_at,
      label: "Stock reserved for this order",
      badge: "Reserved",
      badgeClass: "bg-blue-100 text-blue-800",
    });
  }
  if (order.shipped_at) {
    events.push({
      at: order.shipped_at,
      label: `Marked shipped${order.shipping_profile_name ? ` · ${order.shipping_profile_name}` : ""}`,
      badge: "Shipped",
      badgeClass: "bg-green-100 text-green-800",
    });
  }
  if (order.cancelled_at) {
    events.push({
      at: order.cancelled_at,
      label: "Order cancelled",
      badge: "Cancelled",
      badgeClass: "bg-slate-200 text-slate-600",
    });
  }

  events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());

  return (
    <div className="flex flex-col gap-2 rounded bg-white p-4 text-sm shadow-sm">
      <h2 className="text-sm font-medium text-slate-600">Timeline</h2>
      <ul className="flex flex-col gap-1">
        {events.map((e, i) => (
          <li key={i} className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-xs tabular-nums text-slate-400">
              {formatDayMonth(e.at)}
            </span>
            <span className="min-w-0 flex-1 truncate">{e.label}</span>
            <Badge className={e.badgeClass}>{e.badge}</Badge>
          </li>
        ))}
      </ul>
    </div>
  );
}
