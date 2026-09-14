import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { ordersApi } from "../../api/orders";
import { productsApi } from "../../api/products";
import type { Order, OrderLine } from "../../api/types";
import { Badge } from "../../components/common/Badge";
import { CopyButton } from "../../components/common/CopyButton";
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

// Reorders lines so a substitution's replacement line sits immediately after the line
// it replaced, instead of wherever it happens to fall in the backend's array order.
function sortLinesForDisplay(lines: OrderLine[]): OrderLine[] {
  const byId = new Map(lines.map((line) => [line.id, line]));
  const childrenByParentId = new Map<number, OrderLine[]>();
  for (const line of lines) {
    const parentId = line.substituted_from?.line_id;
    if (parentId != null && byId.has(parentId)) {
      const siblings = childrenByParentId.get(parentId) ?? [];
      siblings.push(line);
      childrenByParentId.set(parentId, siblings);
    }
  }

  const visited = new Set<number>();
  const result: OrderLine[] = [];
  const visit = (line: OrderLine) => {
    if (visited.has(line.id)) return;
    visited.add(line.id);
    result.push(line);
    for (const child of childrenByParentId.get(line.id) ?? []) {
      visit(child);
    }
  };

  for (const line of lines) {
    const parentId = line.substituted_from?.line_id;
    if (parentId == null || !byId.has(parentId)) {
      visit(line);
    }
  }
  return result;
}

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

  const displayLines = sortLinesForDisplay(order.lines);

  const items = order.lines.reduce((sum, l) => sum + l.ordered_qty, 0);
  const discount =
    order.discount_amount != null ? Number(order.discount_amount) : 0;
  const fulfilment = orderFulfilment(order);
  const placed = new Date(order.order_placed_at).toLocaleString();
  const channelLabel = order.platform ? PLATFORM_LABELS[order.platform] : "Manual";
  const isDone = order.status === "shipped" || order.status === "cancelled";
  const isOverdue =
    !isDone &&
    order.ship_by_date != null &&
    new Date(order.ship_by_date).getTime() < Date.now();
  const dueLabel = order.ship_by_date
    ? `Due ${new Date(order.ship_by_date).toLocaleDateString(undefined, { day: "2-digit", month: "short" })}`
    : null;

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
                {dueLabel && (
                  <>
                    {" · "}
                    <span className={isOverdue ? "font-semibold text-red-600" : ""}>
                      {dueLabel}
                    </span>
                  </>
                )}
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
                {displayLines.map((line) => (
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

function UndoIcon() {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 7v6h6" />
      <path d="M3.51 13a9 9 0 1 0 2.13-9.36L3 7" />
    </svg>
  );
}

function ArrowRightIcon() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14" />
      <path d="M13 6l6 6-6 6" />
    </svg>
  );
}

function ArrowLeftIcon() {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M19 12H5" />
      <path d="M11 6l-6 6 6 6" />
    </svg>
  );
}

function MinusIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M5 12h14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
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
  const queryClient = useQueryClient();
  const unassignable = line.allocated_qty - line.shipped_qty;
  const substitutable = line.product_id != null && line.ordered_qty - line.shipped_qty > 0;
  const isFullySubstituted = line.ordered_qty === 0;
  // A row is visually "linked" to another when it's either side of a still-active
  // substitution — the shared connector bar is what makes a split legible across rows
  // that may not be adjacent once other lines exist.
  const isLinked = line.substituted_from != null || line.substituted_to.length > 0;
  const lineValue =
    line.unit_price != null ? Number(line.unit_price) * line.ordered_qty : null;
  const lineCost =
    line.cost_per_unit_snapshot != null
      ? Number(line.cost_per_unit_snapshot) * line.ordered_qty
      : null;

  const undoMutation = useMutation({
    mutationFn: (substitutionId: number) => ordersApi.undoSubstitution(substitutionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", line.order_id] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    },
  });

  const productCellClasses = isFullySubstituted
    ? "border-l-[3px] border-l-slate-200 py-2 pr-2 pl-[9px]"
    : isLinked
      ? "border-l-[3px] border-l-blue-300 py-2 pr-2 pl-[9px]"
      : "p-2";

  const dash = <span className="text-slate-300">—</span>;

  return (
    <tr className={`border-b border-slate-100 ${isFullySubstituted ? "bg-slate-50" : ""}`}>
      <td className={productCellClasses}>
        {line.needs_mapping ? (
          <div className="flex flex-col gap-1">
            <span className="text-amber-700">
              Unmapped SKU: {line.sku ?? "—"}
            </span>
            <UnmappedLineResolver line={line} />
          </div>
        ) : (
          <span className={isFullySubstituted ? "text-slate-400" : ""}>
            {line.product_name ?? "—"}
            {line.variant_name ? ` — ${line.variant_name}` : ""}
          </span>
        )}
        {line.variation_text && (
          <div className="mt-0.5 flex items-center gap-1 text-[11px] font-medium text-slate-500">
            <span>{line.variation_text}</span>
            <CopyButton value={line.variation_text} label="Copy personalization" />
          </div>
        )}
        {line.substituted_from && (
          <div className="mt-1">
            <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold bg-blue-50 text-blue-700">
              <ArrowLeftIcon />
              from {line.substituted_from.variant_name ?? "another variation"}
            </span>
          </div>
        )}
        {line.substituted_to.length > 0 && (
          <div className="mt-1 flex flex-col items-start gap-1">
            {line.substituted_to.map((sub) => (
              <div key={sub.substitution_id} className="flex items-center gap-1.5">
                <span
                  className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                    isFullySubstituted ? "bg-slate-100 text-slate-600" : "bg-blue-50 text-blue-700"
                  }`}
                >
                  {isFullySubstituted ? (
                    <>Fully substituted → {sub.variant_name ?? "another variation"}</>
                  ) : (
                    <>
                      <ArrowRightIcon />
                      {sub.qty} → {sub.variant_name ?? "another variation"}
                    </>
                  )}
                </span>
                <button
                  onClick={() => undoMutation.mutate(sub.substitution_id)}
                  disabled={undoMutation.isPending}
                  className="inline-flex items-center gap-1 rounded border border-slate-300 px-1.5 py-0.5 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <UndoIcon />
                  Undo
                </button>
              </div>
            ))}
          </div>
        )}
        <ErrorBanner error={undoMutation.error} />
      </td>
      <td className="p-2">{isFullySubstituted ? dash : line.ordered_qty}</td>
      <td className="p-2">{isFullySubstituted ? dash : line.allocated_qty}</td>
      <td className="p-2">{isFullySubstituted ? dash : line.shipped_qty}</td>
      <td className="p-2">
        {isFullySubstituted
          ? dash
          : lineValue != null
            ? formatMoney(lineValue.toFixed(2), line.currency ?? currency)
            : "—"}
      </td>
      <td className="p-2">
        {isFullySubstituted ? dash : lineCost != null ? formatMoney(lineCost.toFixed(2), currency) : "—"}
      </td>
      <td className="p-2">
        <div className="flex flex-col items-start gap-1">
          {unassignable > 0 && (
            <button
              onClick={() => onUnassign(unassignable)}
              className="rounded border border-slate-300 px-2 py-1 text-xs"
            >
              Unassign
            </button>
          )}
          {substitutable && <SubstituteLineControl line={line} />}
        </div>
      </td>
    </tr>
  );
}

function SubstituteLineControl({ line }: { line: OrderLine }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [variantId, setVariantId] = useState<number | "">("");
  const maxQty = line.ordered_qty - line.shipped_qty;
  const [qty, setQty] = useState(maxQty);
  const [reason, setReason] = useState("");

  const { data: siblingVariants } = useQuery({
    queryKey: ["products", line.product_id, "variants"],
    queryFn: () => productsApi.listVariants(line.product_id as number),
    enabled: open && line.product_id != null,
  });
  const targets = (siblingVariants ?? []).filter(
    (v) => v.is_active && v.id !== line.variant_id,
  );

  const substituteMutation = useMutation({
    mutationFn: () =>
      ordersApi.substituteLine(line.id, {
        variant_id: variantId as number,
        qty,
        reason: reason || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", line.order_id] });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      setOpen(false);
      setVariantId("");
      setReason("");
    },
  });

  return (
    <div className="relative inline-block">
      <button
        onClick={() => {
          setQty(maxQty);
          setOpen((v) => !v);
        }}
        className="rounded border border-slate-300 px-2 py-1 text-xs"
      >
        Substitute
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-2 w-64 rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-lg">
          <div className="mb-2 text-[11px] font-semibold text-slate-500">
            Substitute · {line.product_name ?? "Product"}
            {line.variant_name ? ` — ${line.variant_name}` : ""}
          </div>
          {targets.length === 0 ? (
            <span className="text-slate-500">No other variations to substitute.</span>
          ) : (
            <>
              <label className="mb-1 block text-[11px] font-semibold text-slate-500">
                Substitute for
              </label>
              <select
                className="w-full rounded border border-slate-300 px-2 py-1.5 text-xs"
                value={variantId}
                onChange={(e) => setVariantId(e.target.value ? Number(e.target.value) : "")}
              >
                <option value="">Choose a variation…</option>
                {targets.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.variant_name}
                  </option>
                ))}
              </select>

              <label className="mb-1 mt-2 block text-[11px] font-semibold text-slate-500">
                Quantity
              </label>
              <div className="flex items-center gap-2">
                <div className="flex items-stretch overflow-hidden rounded border border-slate-300">
                  <button
                    type="button"
                    onClick={() => setQty((q) => Math.max(1, q - 1))}
                    disabled={qty <= 1}
                    className="flex w-7 items-center justify-center border-r border-slate-300 bg-slate-50 text-slate-600 disabled:opacity-40"
                  >
                    <MinusIcon />
                  </button>
                  <div className="flex w-10 items-center justify-center text-sm font-semibold">
                    {qty}
                  </div>
                  <button
                    type="button"
                    onClick={() => setQty((q) => Math.min(maxQty, q + 1))}
                    disabled={qty >= maxQty}
                    className="flex w-7 items-center justify-center border-l border-slate-300 bg-slate-50 text-slate-600 disabled:opacity-40"
                  >
                    <PlusIcon />
                  </button>
                </div>
                <span className="text-[11px] text-slate-400">
                  of {maxQty}
                  {qty !== maxQty && (
                    <>
                      {" · "}
                      <button
                        type="button"
                        onClick={() => setQty(maxQty)}
                        className="font-semibold text-blue-600"
                      >
                        max
                      </button>
                    </>
                  )}
                </span>
              </div>

              <label className="mb-1 mt-2 block text-[11px] font-semibold text-slate-500">
                Reason <span className="font-normal text-slate-400">(optional)</span>
              </label>
              <input
                placeholder="e.g. customer changed mind"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="w-full rounded border border-slate-300 px-2 py-1.5 text-xs"
              />

              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => substituteMutation.mutate()}
                  disabled={!variantId || substituteMutation.isPending}
                  className="flex-1 rounded bg-slate-900 px-2 py-1.5 font-semibold text-white disabled:opacity-50"
                >
                  Substitute
                </button>
                <button
                  onClick={() => setOpen(false)}
                  className="rounded border border-slate-300 px-2 py-1.5 text-slate-700"
                >
                  Cancel
                </button>
              </div>
            </>
          )}
          <ErrorBanner error={substituteMutation.error} />
        </div>
      )}
    </div>
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

  // Grouped by product category (alphabetical, uncategorised last), then products A–Z within
  // each — a flat catalogue-order list made picking the right one on a busy order slow.
  const groupedProducts = useMemo(() => {
    const groups = new Map<string, NonNullable<typeof products>>();
    for (const p of products ?? []) {
      const key = p.product_category_name ?? "";
      const list = groups.get(key) ?? [];
      list.push(p);
      groups.set(key, list);
    }
    return Array.from(groups.keys())
      .sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)))
      .map((key) => ({
        label: key || "Uncategorised",
        products: [...groups.get(key)!].sort((x, y) => x.name.localeCompare(y.name)),
      }));
  }, [products]);

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
          {groupedProducts.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
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
