import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { ordersApi } from "../../api/orders";
import { productsApi } from "../../api/products";
import type { Order, OrderLine } from "../../api/types";
import { Badge } from "../../components/common/Badge";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { Stat } from "../../components/common/Stat";
import { Tabs, type TabDef } from "../../components/common/Tabs";
import { useSiblingNav } from "../../hooks/useSiblingNav";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import {
  SlideOverManagedContext,
  useCommittableDirty,
  useDirtyRegistryApi,
  useManagedSave,
} from "../../hooks/useDirtyRegistry";
import { CancelOrderDialog } from "../../components/orders/CancelOrderDialog";
import { OrderKittingSection } from "../../components/orders/OrderKittingSection";
import { OrderShippingForm } from "../../components/orders/OrderShippingForm";
import { OrderTimeline } from "../../components/orders/OrderTimeline";
import { formatMoney } from "../../lib/money";
import { orderFulfilment } from "../../lib/orderFulfilment";
import { PLATFORM_COLORS, PLATFORM_LABELS } from "../../lib/platforms";
import { STATUS_CLASSES, STATUS_LABELS } from "./route";

const TAB_IDS = ["fulfilment", "financials", "shipping", "timeline"] as const;
type TabId = (typeof TAB_IDS)[number];

export const Route = createFileRoute("/orders/$orderId")({
  component: OrderDetailRoute,
  // The tab lives in the URL so switching one is a real router navigation — the root
  // unsaved-changes blocker then covers leaving a dirty sub-form without this page wiring a
  // guard itself.
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    // "lines" was this tab's id before it was renamed "fulfilment".
    const tab = search.tab === "lines" ? "fulfilment" : search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

// The slide-over replaces every sub-form's own Save button with one footer Save (see
// OrderFooter and useManagedSave); providing the context a layer above the body is what lets
// those forms read it.
function OrderDetailRoute() {
  return (
    <SlideOverManagedContext.Provider value={true}>
      <OrderDetail />
    </SlideOverManagedContext.Provider>
  );
}

function OrderDetail() {
  const { orderId } = Route.useParams();
  const id = Number(orderId);
  const navigate = useNavigate();
  const routeNavigate = Route.useNavigate();
  const requestedTab = Route.useSearch().tab;
  const setActiveTab = (tab: string) =>
    routeNavigate({ search: { tab: tab as TabId } });
  const queryClient = useQueryClient();

  const { data: order } = useQuery({
    queryKey: ["orders", id],
    queryFn: () => ordersApi.get(id),
  });
  const [showCancelDialog, setShowCancelDialog] = useState(false);
  const { prevId, nextId } = useSiblingNav(
    ["orders"],
    id,
    (data) => (data as { items?: { id: number }[] })?.items,
  );
  const closePanel = useCallback(() => navigate({ to: "/orders" }), [navigate]);
  const goPrev = useCallback(
    () =>
      navigate({ to: "/orders/$orderId", params: { orderId: String(prevId) } }),
    [navigate, prevId],
  );
  const goNext = useCallback(
    () =>
      navigate({ to: "/orders/$orderId", params: { orderId: String(nextId) } }),
    [navigate, nextId],
  );

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["orders"] });
    queryClient.invalidateQueries({ queryKey: ["order-counts"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const shipMutation = useMutation({
    mutationFn: () => ordersApi.ship(id),
    onSuccess: invalidate,
  });
  const allocateMutation = useMutation({
    mutationFn: () => ordersApi.allocate(id),
    onSuccess: invalidate,
  });
  const unassignMutation = useMutation({
    mutationFn: ({ lineId, qty }: { lineId: number; qty: number }) =>
      ordersApi.unassignLine(lineId, qty),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: () => ordersApi.remove(id),
    onSuccess: () => {
      invalidate();
      navigate({ to: "/orders" });
    },
  });

  // Editable notes — the one plain field the order carries that PATCH accepts.
  const notesSeed = useMemo(() => order?.notes ?? "", [order?.notes]);
  const {
    value: notes,
    setValue: setNotes,
    markSaved: markNotesSaved,
    revert: revertNotes,
  } = useEditableCopy<string>({
    key: "order-notes",
    label: "Notes",
    initial: "",
    seed: order ? notesSeed : undefined,
    seedKey: id,
  });
  const notesMutation = useMutation({
    mutationFn: () => ordersApi.update(id, { notes: notes || null }),
    onSuccess: () => {
      markNotesSaved();
      queryClient.invalidateQueries({ queryKey: ["orders", id] });
    },
  });
  useManagedSave("order-notes", {
    save: () => notesMutation.mutate(),
    revert: revertNotes,
  });

  if (!order) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  const canShip = order.status === "pending" || order.status === "allocated";
  const canAllocate = order.status === "pending" || order.status === "allocated";
  const anyAllocated = order.lines.some((l) => l.allocated_qty > l.shipped_qty);
  const canCancel = order.status !== "cancelled";
  // Mirrors the backend delete check — nothing allocated or shipped on any line.
  const canDelete = order.lines.every(
    (l) => l.allocated_qty === 0 && l.shipped_qty === 0,
  );

  const activeTab: TabId = requestedTab ?? "fulfilment";
  const tabs: TabDef[] = [
    { id: "fulfilment", label: "Fulfilment" },
    { id: "financials", label: "Financials" },
    { id: "shipping", label: "Shipping" },
    { id: "timeline", label: "Timeline" },
  ];

  const items = order.lines.reduce((sum, l) => sum + l.ordered_qty, 0);
  const discount =
    order.discount_amount != null ? Number(order.discount_amount) : 0;
  const fulfilment = orderFulfilment(order);
  const placed = new Date(order.order_placed_at).toLocaleString();
  const channelLabel = order.platform ? PLATFORM_LABELS[order.platform] : "Manual";

  return (
    <DetailPanel
      title={order.buyer_name ?? order.external_order_id ?? `Order #${order.id}`}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      headerExtra={
        <Badge className={STATUS_CLASSES[order.status]}>
          {STATUS_LABELS[order.status]}
        </Badge>
      }
      footer={
        <OrderFooter
          actions={
            <>
              {canAllocate && (
                <button
                  onClick={() => allocateMutation.mutate()}
                  disabled={allocateMutation.isPending}
                  className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-50"
                >
                  Allocate
                </button>
              )}
              {canShip && (
                <button
                  onClick={() => shipMutation.mutate()}
                  disabled={!anyAllocated || shipMutation.isPending}
                  className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                >
                  Ship
                </button>
              )}
              {canCancel && (
                <button
                  onClick={() => setShowCancelDialog(true)}
                  className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-600"
                >
                  {order.lines.some((l) => l.shipped_qty > 0)
                    ? "Cancel / return"
                    : "Cancel"}
                </button>
              )}
              {canDelete && (
                <button
                  onClick={() => {
                    if (window.confirm("Delete this order? This cannot be undone.")) {
                      deleteMutation.mutate();
                    }
                  }}
                  disabled={deleteMutation.isPending}
                  className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-600 disabled:opacity-50"
                >
                  Delete
                </button>
              )}
            </>
          }
        />
      }
    >
      <div className="flex flex-col gap-6">
        {order.sync_issue && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
            <span className="font-medium">Sync issue: </span>
            {order.sync_issue}
          </div>
        )}
        {order.pending_marketplace_cancellation && (
          <div className="flex items-center justify-between rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
            <span>
              <span className="font-medium">{channelLabel}</span> reports this
              order as cancelled. Nothing has been changed locally — review and
              confirm.
            </span>
            <button
              onClick={() => setShowCancelDialog(true)}
              className="rounded bg-amber-600 px-3 py-1.5 text-white"
            >
              Review
            </button>
          </div>
        )}

        {/* Identity + headline figures — on every tab. */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div
              className={`flex h-16 w-16 shrink-0 items-center justify-center rounded border text-xs font-semibold ${
                order.platform
                  ? PLATFORM_COLORS[order.platform].muted
                  : "border-slate-200 bg-slate-50 text-slate-500"
              }`}
            >
              {channelLabel}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-700">
                Order #{order.id}
                {order.external_order_id ? ` · ${order.external_order_id}` : ""}
              </p>
              <p className="truncate text-[12.5px] text-slate-500">
                {channelLabel} · {placed}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Stat label="Items" value={String(items)} sub={`${order.lines.length} line${order.lines.length === 1 ? "" : "s"}`} />
            <Stat
              label="Order value"
              value={formatMoney(order.grand_total ?? order.subtotal, order.currency)}
              sub={discount > 0 ? `after ${formatMoney(order.discount_amount, order.currency)} discount` : "before fees"}
            />
            <Stat
              label="Fulfilment"
              value={fulfilment.label}
              sub={fulfilment.detail}
              tone="highlight"
              valueClassName={fulfilment.toneClass}
            />
          </div>
        </div>

        <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

        {activeTab === "fulfilment" && (
          <>
            {order.lines.some((l) => l.needs_mapping) && (
              <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                Some lines couldn't be matched to a product — map them below, or
                the order can't reserve stock.
              </div>
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
                    onUnassign={(qty) =>
                      unassignMutation.mutate({ lineId: line.id, qty })
                    }
                  />
                ))}
              </tbody>
            </table>
            <p className="-mt-2 text-xs text-slate-500">
              Value and cost cover all ordered units; the Financials tab counts
              only shipped units.
            </p>

            <OrderKittingSection orderId={id} currency={order.currency} />

            <label className="flex items-start gap-3">
              <span className="mt-1 w-36 shrink-0 text-sm text-slate-600">Notes</span>
              <textarea
                rows={3}
                className="min-w-0 flex-1 resize-y rounded border border-slate-300 px-2 py-1 text-sm"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </label>

            <ErrorBanner
              error={
                shipMutation.error ??
                allocateMutation.error ??
                unassignMutation.error ??
                deleteMutation.error ??
                notesMutation.error
              }
            />
          </>
        )}

        {activeTab === "financials" && <OrderFinancialsPanel order={order} />}

        {activeTab === "shipping" && (
          <OrderShippingForm
            order={order}
            onSaved={() => {
              queryClient.invalidateQueries({ queryKey: ["orders", id] });
              queryClient.invalidateQueries({ queryKey: ["orders"] });
            }}
          />
        )}

        {activeTab === "timeline" && <OrderTimeline order={order} />}

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
    </DetailPanel>
  );
}

/** The persistent footer: order actions on the left, the one Save/Revert on the right. */
function OrderFooter({ actions }: { actions: ReactNode }) {
  const { isDirty } = useCommittableDirty();
  const registry = useDirtyRegistryApi();
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-slate-500">
          {isDirty ? "Unsaved changes" : "No changes"}
        </span>
        <button
          type="button"
          disabled={!isDirty}
          onClick={() => registry.revertDirtyUnder("")}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50"
        >
          Revert
        </button>
        <button
          type="button"
          disabled={!isDirty}
          onClick={() => registry.commitDirtyUnder("")}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </div>
  );
}

function OrderFinancialsPanel({ order }: { order: Order }) {
  const currency = order.currency;

  // What the items came to before the coupon. subtotal is already net of it — on both
  // marketplaces, since the eBay adapter was corrected — so the discount is a breakdown of
  // the first figure rather than another deduction beside it. It used to sit in the row of
  // deductions, which read as though it came off the total a second time, and made the row
  // disagree with the net profit under it for no reason anybody could see.
  const discount =
    order.discount_amount != null ? Number(order.discount_amount) : 0;
  const listPrice =
    discount > 0 && order.subtotal != null
      ? Number(order.subtotal) + discount
      : null;

  return (
    <div className="rounded bg-white p-4 text-sm shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-slate-600">
        Order value &amp; costs
      </h2>
      {/* items-start so a cell with a note under it doesn't stretch the ones beside it —
          every figure stays on the same line, which is what makes the row readable as a sum. */}
      <div className="flex flex-wrap items-start gap-6">
        <div>
          <p className="text-slate-500">Order value paid</p>
          <p>{formatMoney(order.subtotal, currency)}</p>
          {listPrice != null && (
            <p className="text-xs text-slate-400">
              {formatMoney(String(listPrice), currency)} −{" "}
              {formatMoney(order.discount_amount, currency)} discount
            </p>
          )}
        </div>
        <div>
          <p className="text-slate-500">Postage paid</p>
          <p>{formatMoney(order.shipping_charged, currency)}</p>
        </div>
        {order.refunded_amount != null && (
          <div>
            <p className="text-slate-500">Refunded</p>
            <p>-{formatMoney(order.refunded_amount, currency)}</p>
          </div>
        )}
        <div>
          <p className="text-slate-500">Platform fees</p>
          <p>
            {order.platform == null ? (
              "—"
            ) : order.payment_fees != null ? (
              `-${formatMoney(order.payment_fees, currency)}`
            ) : (
              <span
                className="text-slate-500"
                title={`StockSmith has no fee figure from this marketplace yet. Either it hasn't billed the order, or the fee lookup is failing — check Settings > Integrations.`}
              >
                Not reported yet
              </span>
            )}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Postage cost</p>
          <p
            className={
              order.postage_cost_missing ? "text-amber-700" : undefined
            }
          >
            {order.shipping_cost_snapshot != null
              ? `-${formatMoney(order.shipping_cost_snapshot, currency)}`
              : order.postage_cost_missing
                ? "Not recorded"
                : "—"}
          </p>
          {order.shipping_profile_name && (
            <p className="text-xs text-slate-400">
              {order.shipping_profile_name}
            </p>
          )}
        </div>
        <div>
          <p className="text-slate-500">Materials COGS</p>
          <p title="Each line's build-BOM cost per unit across the units that have shipped, frozen when the line was first allocated.">
            {order.materials_cogs != null
              ? `-${formatMoney(order.materials_cogs, currency)}`
              : "—"}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Kitting COGS</p>
          <p title="Packaging actually consumed for this order's shipped units — one box per parcel, not per unit — valued at what each material cost when it was consumed.">
            {order.kitting_cogs != null
              ? `-${formatMoney(order.kitting_cogs, currency)}`
              : "—"}
          </p>
        </div>
        <div>
          <p className="text-slate-500">Net profit</p>
          <p
            className={`font-semibold ${order.net_profit != null && Number(order.net_profit) < 0 ? "text-red-600" : ""}`}
          >
            {order.net_profit != null
              ? formatMoney(order.net_profit, currency)
              : "—"}
          </p>
          {order.cogs_pending && (
            <p className="text-xs text-amber-700">
              COGS pending — one or more lines haven't been allocated yet, so
              this figure doesn't include their cost.
            </p>
          )}
          {order.postage_cost_missing && (
            <p className="text-xs text-amber-700">
              No postage cost — this order shipped without a shipping profile,
              so this figure doesn't deduct what postage cost. Assign the
              product a shipping profile so future orders capture it.
            </p>
          )}
        </div>
      </div>
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
  const lineValue =
    line.unit_price != null ? Number(line.unit_price) * line.ordered_qty : null;
  const lineCost =
    line.cost_per_unit_snapshot != null
      ? Number(line.cost_per_unit_snapshot) * line.ordered_qty
      : null;
  return (
    <tr className="border-b border-slate-100">
      <td className="p-2">
        {line.needs_mapping ? (
          <div className="flex flex-col gap-1">
            <span className="text-amber-700">
              Unmapped SKU: {line.sku ?? "—"}
            </span>
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
      <td className="p-2">
        {lineValue != null
          ? formatMoney(lineValue.toFixed(2), line.currency ?? currency)
          : "—"}
      </td>
      <td className="p-2">
        {lineCost != null ? formatMoney(lineCost.toFixed(2), currency) : "—"}
      </td>
      <td className="p-2">
        {unassignable > 0 && (
          <button
            onClick={() => onUnassign(unassignable)}
            className="rounded border border-slate-300 px-2 py-1 text-xs"
          >
            Unassign
          </button>
        )}
      </td>
    </tr>
  );
}

function UnmappedLineResolver({ line }: { line: OrderLine }) {
  const queryClient = useQueryClient();
  const { data: products } = useQuery({
    queryKey: ["products"],
    queryFn: productsApi.list,
  });
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
    mutationFn: () =>
      ordersApi.createProductAndMap(line.id, {
        name: newName,
        sku: newSku || null,
      }),
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
            onChange={(e) =>
              setVariantId(e.target.value ? Number(e.target.value) : "")
            }
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
