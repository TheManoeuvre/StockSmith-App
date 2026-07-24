import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ordersApi } from "../../api/orders";
import { productsApi } from "../../api/products";
import { shippingProfilesApi } from "../../api/shippingProfiles";
import type { Order, OrderLine, OrderStatus } from "../../api/types";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CancelOrderDialog } from "../../components/orders/CancelOrderDialog";
import { OrderKittingSection } from "../../components/orders/OrderKittingSection";
import { formatMoney } from "../../lib/money";
import { PLATFORM_LABELS } from "../../lib/platforms";

export const Route = createFileRoute("/orders/$orderId")({
  component: OrderDetail,
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

function OrderDetail() {
  const { orderId } = Route.useParams();
  const id = Number(orderId);
  const queryClient = useQueryClient();

  const { data: order } = useQuery({ queryKey: ["orders", id], queryFn: () => ordersApi.get(id) });
  const [showCancelDialog, setShowCancelDialog] = useState(false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["orders", id] });
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const shipMutation = useMutation({ mutationFn: () => ordersApi.ship(id), onSuccess: invalidate });
  const allocateMutation = useMutation({ mutationFn: () => ordersApi.allocate(id), onSuccess: invalidate });
  const unassignMutation = useMutation({
    mutationFn: ({ lineId, qty }: { lineId: number; qty: number }) => ordersApi.unassignLine(lineId, qty),
    onSuccess: invalidate,
  });

  if (!order) return <p>Loading…</p>;

  const canCancel = order.status !== "cancelled";
  const canShip = order.status === "pending" || order.status === "allocated";
  const canAllocate = order.status === "pending" || order.status === "allocated";
  const anyAllocated = order.lines.some((l) => l.allocated_qty > l.shipped_qty);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          {order.buyer_name ?? order.external_order_id ?? `Order #${order.id}`}
        </h1>
        <span className={`rounded px-2 py-0.5 text-xs ${STATUS_CLASSES[order.status]}`}>
          {STATUS_LABELS[order.status]}
        </span>
      </div>

      {order.sync_issue && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          <span className="font-medium">Sync issue: </span>
          {order.sync_issue}
        </div>
      )}

      {order.pending_marketplace_cancellation && (
        <div className="flex items-center justify-between rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          <span>
            <span className="font-medium">
              {order.platform ? PLATFORM_LABELS[order.platform] : "The marketplace"}
            </span>{" "}
            reports this order as cancelled. Nothing has been changed locally — review and confirm below.
          </span>
          <button
            onClick={() => setShowCancelDialog(true)}
            className="rounded bg-amber-600 px-3 py-1.5 text-white"
          >
            Review cancellation
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-6 rounded bg-white p-4 text-sm shadow-sm">
        <div>
          <p className="text-slate-500">Order #</p>
          <p>{order.id}</p>
        </div>
        <div>
          <p className="text-slate-500">Platform</p>
          <p>{order.platform ? PLATFORM_LABELS[order.platform] : "Manual"}{order.external_order_id ? ` (${order.external_order_id})` : ""}</p>
        </div>
        <div>
          <p className="text-slate-500">Placed</p>
          <p>{new Date(order.order_placed_at).toLocaleString()}</p>
        </div>
        {order.notes && (
          <div>
            <p className="text-slate-500">Notes</p>
            <p>{order.notes}</p>
          </div>
        )}
      </div>

      <OrderFinancialsPanel order={order} />

      {order.platform === null && order.status !== "shipped" && (
        <ManualShippingEditor order={order} onSaved={invalidate} />
      )}

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Product</th>
            <th className="p-2">Ordered</th>
            <th className="p-2">Allocated</th>
            <th className="p-2">Shipped</th>
            <th className="p-2">Value</th>
            <th className="p-2">Cost</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {order.lines.map((line) => (
            <OrderLineRow
              key={line.id}
              line={line}
              currency={order.currency}
              onUnassign={(qty) => unassignMutation.mutate({ lineId: line.id, qty })}
            />
          ))}
        </tbody>
      </table>

      <OrderKittingSection orderId={id} />

      <div className="flex gap-2">
        {canAllocate && (
          <button
            onClick={() => allocateMutation.mutate()}
            disabled={allocateMutation.isPending}
            className="rounded border border-slate-300 px-4 py-2 disabled:opacity-50"
          >
            Allocate stock
          </button>
        )}
        {canShip && (
          <button
            onClick={() => shipMutation.mutate()}
            disabled={!anyAllocated}
            className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
          >
            Ship allocated units
          </button>
        )}
        {canCancel && (
          <button
            onClick={() => setShowCancelDialog(true)}
            className="rounded border border-red-300 px-4 py-2 text-red-600"
          >
            {order.lines.some((l) => l.shipped_qty > 0) ? "Cancel / process return" : "Cancel order"}
          </button>
        )}
      </div>
      <ErrorBanner error={shipMutation.error ?? allocateMutation.error ?? unassignMutation.error} />

      {showCancelDialog && (
        <CancelOrderDialog
          orderId={id}
          onClose={() => setShowCancelDialog(false)}
          onCancelled={() => {
            setShowCancelDialog(false);
            invalidate();
          }}
        />
      )}
    </div>
  );
}

function OrderFinancialsPanel({ order }: { order: Order }) {
  const currency = order.currency;
  const materialsCogsTotal = order.lines.reduce((sum, line) => {
    if (line.cost_per_unit_snapshot == null && line.kitting_cost_per_unit_snapshot == null) return sum;
    const perUnit = Number(line.cost_per_unit_snapshot ?? 0) + Number(line.kitting_cost_per_unit_snapshot ?? 0);
    return sum + perUnit * line.ordered_qty;
  }, 0);
  const hasMaterialsCogs = order.lines.some(
    (l) => l.cost_per_unit_snapshot != null || l.kitting_cost_per_unit_snapshot != null
  );

  return (
    <div className="rounded bg-white p-4 text-sm shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-slate-600">Order value &amp; costs</h2>
      <div className="flex flex-wrap gap-6">
        <div>
          <p className="text-slate-500">Order value paid</p>
          <p>{formatMoney(order.subtotal, currency)}</p>
        </div>
        <div>
          <p className="text-slate-500">Postage paid</p>
          <p>{formatMoney(order.shipping_charged, currency)}</p>
        </div>
        {order.discount_amount != null && (
          <div>
            <p className="text-slate-500">Discount</p>
            <p>-{formatMoney(order.discount_amount, currency)}</p>
          </div>
        )}
        {order.refunded_amount != null && (
          <div>
            <p className="text-slate-500">Refunded</p>
            <p>-{formatMoney(order.refunded_amount, currency)}</p>
          </div>
        )}
        <div>
          <p className="text-slate-500">Platform fees</p>
          <p>
            {order.platform == null
              ? "—"
              : order.payment_fees != null
                ? `-${formatMoney(order.payment_fees, currency)}`
                : "Not yet settled"}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Postage cost{order.shipping_profile_name ? ` (${order.shipping_profile_name})` : ""}</p>
          <p>{order.shipping_cost_snapshot != null ? `-${formatMoney(order.shipping_cost_snapshot, currency)}` : "—"}</p>
        </div>
        <div>
          <p className="text-slate-500">Cost of goods (materials + kitting)</p>
          <p>{hasMaterialsCogs ? `-${formatMoney(materialsCogsTotal.toFixed(2), currency)}` : "—"}</p>
        </div>
        <div>
          <p className="text-slate-500">Net profit</p>
          <p className={`font-semibold ${order.net_profit != null && Number(order.net_profit) < 0 ? "text-red-600" : ""}`}>
            {order.net_profit != null ? formatMoney(order.net_profit, currency) : "—"}
          </p>
        </div>
      </div>
    </div>
  );
}

function ManualShippingEditor({ order, onSaved }: { order: Order; onSaved: () => void }) {
  const { data: shippingProfiles } = useQuery({
    queryKey: ["settings", "shipping-profiles"],
    queryFn: shippingProfilesApi.list,
  });
  const profiles = shippingProfiles ?? [];

  const [shippingProfileId, setShippingProfileId] = useState(
    order.shipping_profile_id != null ? String(order.shipping_profile_id) : ""
  );
  const [shippingCharged, setShippingCharged] = useState(order.shipping_charged ?? "");

  const saveMutation = useMutation({
    mutationFn: () =>
      ordersApi.update(order.id, {
        shipping_profile_id: shippingProfileId ? Number(shippingProfileId) : null,
        shipping_charged: shippingCharged || null,
      }),
    onSuccess: onSaved,
  });

  return (
    <div className="rounded bg-white p-4 text-sm shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-slate-600">Shipping</h2>
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">Shipping profile</span>
          <select
            className="w-48 rounded border border-slate-300 px-2 py-1"
            value={shippingProfileId}
            onChange={(e) => {
              const id = e.target.value;
              setShippingProfileId(id);
              const profile = profiles.find((p) => String(p.id) === id);
              if (profile) setShippingCharged(profile.price);
            }}
          >
            <option value="">No profile</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-500">Shipping charged</span>
          <input
            className="w-28 rounded border border-slate-300 px-2 py-1"
            placeholder="0.00"
            value={shippingCharged}
            onChange={(e) => setShippingCharged(e.target.value)}
          />
        </label>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50"
        >
          Save
        </button>
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}

function OrderLineRow({
  line,
  currency,
  onUnassign,
}: {
  line: OrderLine;
  currency: string | null;
  onUnassign: (qty: number) => void;
}) {
  const unassignable = line.allocated_qty - line.shipped_qty;
  const lineValue = line.unit_price != null ? Number(line.unit_price) * line.ordered_qty : null;
  const lineCost =
    line.cost_per_unit_snapshot != null || line.kitting_cost_per_unit_snapshot != null
      ? (Number(line.cost_per_unit_snapshot ?? 0) + Number(line.kitting_cost_per_unit_snapshot ?? 0)) * line.ordered_qty
      : null;
  return (
    <tr className="border-b border-slate-100">
      <td className="p-2">
        {line.needs_mapping ? (
          <div className="flex flex-col gap-1">
            <span className="text-amber-700">Unmapped SKU: {line.sku ?? "—"}</span>
            <UnmappedLineResolver line={line} />
          </div>
        ) : (
          <>
            {line.product_name ?? "—"}
            {line.variant_name ? ` — ${line.variant_name}` : ""}
          </>
        )}
      </td>
      <td className="p-2">{line.ordered_qty}</td>
      <td className="p-2">{line.allocated_qty}</td>
      <td className="p-2">{line.shipped_qty}</td>
      <td className="p-2">{lineValue != null ? formatMoney(lineValue.toFixed(2), line.currency ?? currency) : "—"}</td>
      <td className="p-2">{lineCost != null ? formatMoney(lineCost.toFixed(2), currency) : "—"}</td>
      <td className="p-2">
        {unassignable > 0 && (
          <button onClick={() => onUnassign(unassignable)} className="rounded border border-slate-300 px-2 py-1 text-xs">
            Unassign
          </button>
        )}
      </td>
    </tr>
  );
}

function UnmappedLineResolver({ line }: { line: OrderLine }) {
  const queryClient = useQueryClient();
  const { data: products } = useQuery({ queryKey: ["products"], queryFn: productsApi.list });
  const [productId, setProductId] = useState<number | "">("");
  const [variantId, setVariantId] = useState<number | "">("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSku, setNewSku] = useState(line.sku ?? "");

  const { data: variants } = useQuery({
    queryKey: ["products", productId, "variants"],
    queryFn: () => productsApi.listVariants(productId as number),
    enabled: typeof productId === "number",
  });

  const onResolved = () => {
    queryClient.invalidateQueries({ queryKey: ["orders", line.order_id] });
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const mapMutation = useMutation({
    mutationFn: () =>
      ordersApi.mapSku(line.id, {
        product_id: variantId ? undefined : (productId as number),
        variant_id: variantId ? (variantId as number) : undefined,
      }),
    onSuccess: onResolved,
  });

  const createMutation = useMutation({
    mutationFn: () => ordersApi.createProductAndMap(line.id, { name: newName, sku: newSku || null }),
    onSuccess: onResolved,
  });

  return (
    <div className="flex flex-col gap-1 rounded border border-amber-200 bg-amber-50 p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="rounded border border-slate-300 px-2 py-1"
          value={productId}
          onChange={(e) => {
            setProductId(e.target.value ? Number(e.target.value) : "");
            setVariantId("");
          }}
        >
          <option value="">Select product…</option>
          {products?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {variants && variants.length > 0 && (
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={variantId}
            onChange={(e) => setVariantId(e.target.value ? Number(e.target.value) : "")}
          >
            <option value="">(no variant)</option>
            {variants.map((v) => (
              <option key={v.id} value={v.id}>
                {v.variant_name}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={() => mapMutation.mutate()}
          disabled={!productId || mapMutation.isPending}
          className="rounded bg-slate-900 px-2 py-1 text-white disabled:opacity-50"
        >
          Assign
        </button>
        <button
          onClick={() => setShowCreateForm((v) => !v)}
          className="rounded border border-slate-300 px-2 py-1"
        >
          Add to StockSmith
        </button>
      </div>
      <ErrorBanner error={mapMutation.error} />
      {showCreateForm && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="Product name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            className="rounded border border-slate-300 px-2 py-1"
            placeholder="SKU (optional)"
            value={newSku}
            onChange={(e) => setNewSku(e.target.value)}
          />
          <button
            onClick={() => createMutation.mutate()}
            disabled={!newName || createMutation.isPending}
            className="rounded bg-slate-900 px-2 py-1 text-white disabled:opacity-50"
          >
            Create &amp; assign
          </button>
        </div>
      )}
      <ErrorBanner error={createMutation.error} />
    </div>
  );
}
