import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { materialsApi } from "../../api/materials";
import {
  purchasesApi,
  type PurchaseLineInput,
  type ReceiptLineInput,
} from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { PurchaseLineEditor } from "../../components/purchases/PurchaseLineEditor";
import { PurchaseStatusPill } from "../../components/purchases/PurchaseStatusPill";
import { ReceiptHistoryPanel } from "../../components/purchases/ReceiptHistoryPanel";
import { ReceiveDialog } from "../../components/purchases/ReceiveDialog";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { ConfirmDialog } from "../../components/common/ConfirmDialog";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { FieldRow } from "../../components/common/FieldRow";
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
import { formatMoney } from "../../lib/money";
import { displayQty, formatDayMonth } from "../../lib/format";

const TAB_IDS = ["lines", "receiving"] as const;
type TabId = (typeof TAB_IDS)[number];

const TABS: TabDef[] = [
  { id: "lines", label: "Lines" },
  { id: "receiving", label: "Receiving history" },
];

export const Route = createFileRoute("/purchases/$purchaseId")({
  component: PurchaseDetailRoute,
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    const tab = search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

interface DetailsForm {
  supplier: string;
  supplierId: number | null;
  supplierOrderNumber: string;
  orderDate: string;
  expectedArrivalDate: string;
  notes: string;
  deliveryCost: string;
}

// The slide-over replaces the per-section Save buttons with one footer Save (see
// PurchaseFooter / useManagedSave); the context has to be provided a layer above the body.
function PurchaseDetailRoute() {
  return (
    <SlideOverManagedContext.Provider value={true}>
      <PurchaseDetail />
    </SlideOverManagedContext.Provider>
  );
}

function PurchaseDetail() {
  const { purchaseId } = Route.useParams();
  const id = Number(purchaseId);
  const navigate = useNavigate();
  const routeNavigate = Route.useNavigate();
  const activeTab: TabId = Route.useSearch().tab ?? "lines";
  const setActiveTab = (tab: string) =>
    routeNavigate({ search: { tab: tab as TabId } });
  const queryClient = useQueryClient();

  const { data: purchase } = useQuery({
    queryKey: ["purchases", id],
    queryFn: () => purchasesApi.get(id),
  });
  const { prevId, nextId } = useSiblingNav(
    ["purchases"],
    id,
    (data) => data as { id: number }[] | undefined,
  );
  const closePanel = useCallback(() => navigate({ to: "/purchases" }), [navigate]);
  const goPrev = useCallback(
    () =>
      navigate({
        to: "/purchases/$purchaseId",
        params: { purchaseId: String(prevId) },
      }),
    [navigate, prevId],
  );
  const goNext = useCallback(
    () =>
      navigate({
        to: "/purchases/$purchaseId",
        params: { purchaseId: String(nextId) },
      }),
    [navigate, nextId],
  );
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
  });
  const { data: suppliers } = useQuery({
    queryKey: ["suppliers"],
    queryFn: suppliersApi.list,
  });

  const [receiving, setReceiving] = useState(false);
  const [closingLineId, setClosingLineId] = useState<number | null>(null);
  const [closeApportion, setCloseApportion] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["purchases", id] });
    queryClient.invalidateQueries({ queryKey: ["purchases"] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    queryClient.invalidateQueries({ queryKey: ["suppliers"] });
  };

  // --- Details form (Supplier / dates / notes) -------------------------------------------
  const detailsSeed = useMemo<DetailsForm | undefined>(
    () =>
      purchase
        ? {
            supplier: purchase.supplier_name ?? "",
            supplierId: purchase.supplier_id,
            supplierOrderNumber: purchase.supplier_order_number ?? "",
            orderDate: purchase.order_date,
            expectedArrivalDate: purchase.expected_arrival_date ?? "",
            notes: purchase.notes ?? "",
            deliveryCost: purchase.delivery_cost ?? "",
          }
        : undefined,
    [purchase],
  );
  const {
    value: details,
    setValue: setDetails,
    markSaved: markDetailsSaved,
    revert: revertDetails,
  } = useEditableCopy<DetailsForm>({
    key: "purchase-details",
    label: "Purchase details",
    initial: {
      supplier: "",
      supplierId: null,
      supplierOrderNumber: "",
      orderDate: "",
      expectedArrivalDate: "",
      notes: "",
      deliveryCost: "",
    },
    seed: detailsSeed,
    seedKey: id,
  });
  const setDetailsField = <K extends keyof DetailsForm>(k: K, v: DetailsForm[K]) =>
    setDetails((prev) => ({ ...prev, [k]: v }));

  const detailsMutation = useMutation({
    mutationFn: async () => {
      let resolvedSupplierId = details.supplierId;
      if (!resolvedSupplierId && details.supplier.trim()) {
        resolvedSupplierId = (
          await suppliersApi.findOrCreate(details.supplier.trim())
        ).id;
      }
      return purchasesApi.update(id, {
        supplier_id: resolvedSupplierId,
        supplier_order_number: details.supplierOrderNumber.trim() || null,
        order_date: details.orderDate || null,
        expected_arrival_date: details.expectedArrivalDate || null,
        notes: details.notes || null,
        delivery_cost: details.deliveryCost.trim() || null,
      });
    },
    onSuccess: () => {
      markDetailsSaved();
      invalidate();
    },
  });
  useManagedSave("purchase-details", {
    save: () => detailsMutation.mutate(),
    revert: revertDetails,
  });

  // --- Order lines ---------------------------------------------------------------------
  const linesSeed = useMemo<PurchaseLineInput[] | undefined>(
    () =>
      purchase
        ? // Carry each id through — without it a save reads as "replace every line",
          // which the backend refuses once anything has been received.
          purchase.lines.map((l) => ({
            id: l.id,
            material_id: l.material_id,
            // "30.0000" -> "30". Kept in step with what the editor shows so an untouched
            // line doesn't read as dirty; carry notes through untouched even though the
            // editor no longer surfaces them, so an unrelated save can't wipe them.
            qty: displayQty(l.qty),
            total_cost: l.total_cost,
            notes: l.notes,
          }))
        : undefined,
    [purchase],
  );
  const {
    value: lines,
    setValue: setLines,
    markSaved: markLinesSaved,
    revert: revertLines,
  } = useEditableCopy<PurchaseLineInput[]>({
    key: "purchase-lines",
    label: "Order lines",
    initial: [],
    seed: linesSeed,
    seedKey: id,
  });
  const linesMutation = useMutation({
    mutationFn: () => purchasesApi.replaceLines(id, lines),
    onSuccess: () => {
      markLinesSaved();
      invalidate();
    },
  });
  useManagedSave("purchase-lines", {
    save: () => linesMutation.mutate(),
    revert: revertLines,
  });

  // --- Actions ------------------------------------------------------------------------
  const receiptsMutation = useMutation({
    mutationFn: ({
      receivedAt,
      lines: receiptLines,
    }: {
      receivedAt: string;
      lines: ReceiptLineInput[];
    }) =>
      purchasesApi.createReceipts(id, {
        received_at: receivedAt,
        lines: receiptLines,
      }),
    onSuccess: () => {
      setReceiving(false);
      invalidate();
    },
  });
  const undoBatchMutation = useMutation({
    mutationFn: (batchId: string) => purchasesApi.deleteReceiptBatch(id, batchId),
    onSuccess: invalidate,
  });
  const undoReceiptMutation = useMutation({
    mutationFn: (receiptId: number) => purchasesApi.deleteReceipt(id, receiptId),
    onSuccess: invalidate,
  });
  const closeLineMutation = useMutation({
    mutationFn: ({ lineId, apportion }: { lineId: number; apportion: boolean }) =>
      purchasesApi.closeLine(id, lineId, apportion),
    onSuccess: invalidate,
  });
  const reopenLineMutation = useMutation({
    mutationFn: (lineId: number) => purchasesApi.reopenLine(id, lineId),
    onSuccess: invalidate,
  });
  const unreceiveMutation = useMutation({
    mutationFn: () => purchasesApi.unreceive(id),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: () => purchasesApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      navigate({ to: "/purchases" });
    },
  });

  if (!purchase) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  const deliveryNum = Number(purchase.delivery_cost ?? 0);
  const orderTotal =
    purchase.lines.reduce((s, l) => s + Number(l.total_cost), 0) + deliveryNum;
  const outstanding = purchase.lines.filter(
    (l) => Number(l.outstanding_qty) > 0,
  ).length;
  const receivedPct =
    purchase.lines.length === 0
      ? 0
      : Math.round(
          (purchase.lines.reduce(
            (s, l) => s + (Number(l.qty) ? Number(l.received_qty) / Number(l.qty) : 0),
            0,
          ) /
            purchase.lines.length) *
            100,
        );
  const statusLabel =
    purchase.status === "received"
      ? "Received"
      : purchase.status === "partially_received"
        ? "Part received"
        : "Ordered";

  return (
    <DetailPanel
      title={`Purchase #${purchase.id}`}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      headerExtra={<PurchaseStatusPill status={purchase.status} />}
      footer={
        <PurchaseFooter
          actions={
            <>
              {purchase.status !== "received" && (
                <button
                  onClick={() => setReceiving(true)}
                  className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
                >
                  Record a delivery
                </button>
              )}
              {purchase.status !== "ordered" && (
                <button
                  onClick={() => unreceiveMutation.mutate()}
                  className="rounded border border-slate-300 px-3 py-1.5 text-sm"
                >
                  Undo all deliveries
                </button>
              )}
              <button
                onClick={() => setConfirmingDelete(true)}
                className="rounded border border-red-300 px-3 py-1.5 text-sm text-red-600"
              >
                Delete
              </button>
            </>
          }
        />
      }
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="h-14 w-14 shrink-0 rounded border border-slate-200 bg-slate-50" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-700">
                {purchase.supplier_name ?? "No supplier"}
              </p>
              <p className="truncate text-[12.5px] text-slate-500">
                {purchase.supplier_order_number
                  ? `Order ${purchase.supplier_order_number} · `
                  : ""}
                Placed {formatDayMonth(purchase.order_date)}
                {purchase.expected_arrival_date
                  ? ` · due ${formatDayMonth(purchase.expected_arrival_date)}`
                  : ""}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Stat
              label="Lines"
              value={String(purchase.lines.length)}
              sub={`${outstanding} outstanding`}
            />
            <Stat
              label="Order total"
              value={formatMoney(String(orderTotal), "GBP")}
              sub={
                deliveryNum > 0
                  ? `incl ${formatMoney(String(deliveryNum), "GBP")} delivery`
                  : "no delivery charge"
              }
            />
            <Stat
              label="Received"
              value={`${receivedPct}%`}
              sub={statusLabel}
              tone="highlight"
            />
          </div>
        </div>

        <form
          className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            detailsMutation.mutate();
          }}
        >
          <FieldRow label="Supplier">
            <CreatableSelect
              className="rounded border border-slate-300 px-2 py-1"
              options={suppliers ?? []}
              value={details.supplier}
              onChange={(v) => setDetailsField("supplier", v)}
              onResolved={(v) => setDetailsField("supplierId", v)}
            />
          </FieldRow>
          <FieldRow label="Supplier order #">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={details.supplierOrderNumber}
              onChange={(e) =>
                setDetailsField("supplierOrderNumber", e.target.value)
              }
              placeholder="Their PO / order number"
            />
          </FieldRow>
          <FieldRow label="Order date">
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={details.orderDate}
              onChange={(e) => setDetailsField("orderDate", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Expected arrival">
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={details.expectedArrivalDate}
              onChange={(e) =>
                setDetailsField("expectedArrivalDate", e.target.value)
              }
            />
          </FieldRow>
          <FieldRow label="Notes">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={details.notes}
              onChange={(e) => setDetailsField("notes", e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Delivery cost">
            <div className="flex items-center gap-1">
              <span className="text-[11px] text-slate-400">£</span>
              <input
                type="number"
                step="0.01"
                className="w-28 rounded border border-slate-300 px-2 py-1 text-right tabular-nums"
                value={details.deliveryCost}
                onChange={(e) => setDetailsField("deliveryCost", e.target.value)}
                placeholder="0.00"
              />
            </div>
          </FieldRow>
        </form>

        <Tabs tabs={TABS} active={activeTab} onChange={setActiveTab} />

        {activeTab === "lines" && (
          <PurchaseLineEditor
            materials={materials ?? []}
            lines={lines}
            saved={purchase.lines}
            onChange={setLines}
            onCloseLine={(lineId) => {
              setCloseApportion(false);
              setClosingLineId(lineId);
            }}
            onReopenLine={(lineId) => reopenLineMutation.mutate(lineId)}
          />
        )}

        {activeTab === "receiving" && (
          <ReceiptHistoryPanel
            purchase={purchase}
            materials={materials ?? []}
            busy={undoBatchMutation.isPending || undoReceiptMutation.isPending}
            onUndoBatch={(batchId) => undoBatchMutation.mutate(batchId)}
            onUndoReceipt={(receiptId) => undoReceiptMutation.mutate(receiptId)}
          />
        )}

        <ErrorBanner
          error={
            detailsMutation.error ??
            linesMutation.error ??
            unreceiveMutation.error ??
            undoBatchMutation.error ??
            undoReceiptMutation.error ??
            closeLineMutation.error ??
            reopenLineMutation.error ??
            deleteMutation.error
          }
        />

        <ConfirmDialog
          open={closingLineId !== null}
          title="Close this line short?"
          tone="default"
          body={
            <div className="flex flex-col gap-3">
              <p>
                Nothing more is expected on this line, so it stops counting as on
                order and the purchase can complete. What was ordered stays on the
                record.
              </p>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={closeApportion}
                  onChange={(e) => setCloseApportion(e.target.checked)}
                />
                <span>
                  The supplier charged for the full quantity anyway — put the whole
                  line cost on what actually arrived, rather than only its share.
                </span>
              </label>
            </div>
          }
          confirmLabel="Close line"
          busy={closeLineMutation.isPending}
          onConfirm={() => {
            closeLineMutation.mutate({
              lineId: closingLineId!,
              apportion: closeApportion,
            });
            setClosingLineId(null);
          }}
          onCancel={() => setClosingLineId(null)}
        />

        <ConfirmDialog
          open={confirmingDelete}
          title="Delete this purchase?"
          tone="danger"
          body="The order and its line history are removed. Deliveries already recorded against it must be undone first."
          confirmLabel="Delete purchase"
          busy={deleteMutation.isPending}
          onConfirm={() => {
            setConfirmingDelete(false);
            deleteMutation.mutate();
          }}
          onCancel={() => setConfirmingDelete(false)}
        />

        {receiving && (
          <ReceiveDialog
            purchase={purchase}
            materials={materials ?? []}
            busy={receiptsMutation.isPending}
            error={receiptsMutation.error}
            onSubmit={(receivedAt, receiptLines) =>
              receiptsMutation.mutate({ receivedAt, lines: receiptLines })
            }
            onClose={() => setReceiving(false)}
          />
        )}
      </div>
    </DetailPanel>
  );
}

/** The persistent footer: purchase actions on the left, one Save/Revert on the right. */
function PurchaseFooter({ actions }: { actions: ReactNode }) {
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
