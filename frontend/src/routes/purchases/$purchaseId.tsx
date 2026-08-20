import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { materialsApi } from "../../api/materials";
import { purchasesApi, type PurchaseLineInput, type ReceiptLineInput } from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { PurchaseLineEditor } from "../../components/purchases/PurchaseLineEditor";
import { PurchaseStatusPill } from "../../components/purchases/PurchaseStatusPill";
import { ReceiptHistoryPanel } from "../../components/purchases/ReceiptHistoryPanel";
import { ReceiveDialog } from "../../components/purchases/ReceiveDialog";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { ConfirmDialog } from "../../components/common/ConfirmDialog";
import { CreatableSelect } from "../../components/common/CreatableSelect";

export const Route = createFileRoute("/purchases/$purchaseId")({
  component: PurchaseDetail,
});

function PurchaseDetail() {
  const { purchaseId } = Route.useParams();
  const id = Number(purchaseId);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: purchase } = useQuery({ queryKey: ["purchases", id], queryFn: () => purchasesApi.get(id) });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: suppliers } = useQuery({ queryKey: ["suppliers"], queryFn: suppliersApi.list });

  const [supplier, setSupplier] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [orderDate, setOrderDate] = useState("");
  const [expectedArrivalDate, setExpectedArrivalDate] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([]);
  const [receiving, setReceiving] = useState(false);
  const [closingLineId, setClosingLineId] = useState<number | null>(null);
  const [closeApportion, setCloseApportion] = useState(false);

  useEffect(() => {
    if (purchase) {
      setSupplier(purchase.supplier_name ?? "");
      setSupplierId(purchase.supplier_id);
      setOrderDate(purchase.order_date);
      setExpectedArrivalDate(purchase.expected_arrival_date ?? "");
      setNotes(purchase.notes ?? "");
      // Carry the id through. Without it the save reads as "replace every line with these
      // new ones", and the backend refuses rather than orphaning what has been received.
      setLines(
        purchase.lines.map((l) => ({
          id: l.id,
          material_id: l.material_id,
          qty: l.qty,
          total_cost: l.total_cost,
          notes: l.notes,
        })),
      );
    }
  }, [purchase]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["purchases", id] });
    queryClient.invalidateQueries({ queryKey: ["purchases"] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    queryClient.invalidateQueries({ queryKey: ["suppliers"] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      let resolvedSupplierId = supplierId;
      if (!resolvedSupplierId && supplier.trim()) {
        resolvedSupplierId = (await suppliersApi.findOrCreate(supplier.trim())).id;
      }
      await purchasesApi.update(id, {
        supplier_id: resolvedSupplierId,
        order_date: orderDate || null,
        expected_arrival_date: expectedArrivalDate || null,
        notes: notes || null,
      });
      await purchasesApi.replaceLines(id, lines);
    },
    onSuccess: invalidate,
  });

  const receiptsMutation = useMutation({
    mutationFn: ({ receivedAt, lines: receiptLines }: { receivedAt: string; lines: ReceiptLineInput[] }) =>
      purchasesApi.createReceipts(id, { received_at: receivedAt, lines: receiptLines }),
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
  const unreceiveMutation = useMutation({ mutationFn: () => purchasesApi.unreceive(id), onSuccess: invalidate });
  const deleteMutation = useMutation({
    mutationFn: () => purchasesApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      navigate({ to: "/purchases" });
    },
  });

  if (!purchase) return <p>Loading…</p>;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Purchase #{purchase.id}</h1>
        <PurchaseStatusPill status={purchase.status} />
      </div>

      <div className="flex flex-wrap gap-4 rounded bg-white p-4 shadow-sm">
        <label className="flex flex-col gap-1">
          <span className="text-sm">Supplier</span>
          <CreatableSelect
            className="rounded border border-slate-300 px-2 py-1"
            options={suppliers ?? []}
            value={supplier}
            onChange={setSupplier}
            onResolved={setSupplierId}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Order date</span>
          <input
            type="date"
            className="rounded border border-slate-300 px-2 py-1"
            value={orderDate}
            onChange={(e) => setOrderDate(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm">Expected arrival</span>
          <input
            type="date"
            className="rounded border border-slate-300 px-2 py-1"
            value={expectedArrivalDate}
            onChange={(e) => setExpectedArrivalDate(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Notes</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>

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

      <ReceiptHistoryPanel
        purchase={purchase}
        materials={materials ?? []}
        busy={undoBatchMutation.isPending || undoReceiptMutation.isPending}
        onUndoBatch={(batchId) => undoBatchMutation.mutate(batchId)}
        onUndoReceipt={(receiptId) => undoReceiptMutation.mutate(receiptId)}
      />

      <div className="flex gap-2">
        <button
          onClick={() => saveMutation.mutate()}
          disabled={lines.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Save changes
        </button>
        {purchase.status !== "received" && (
          <button
            onClick={() => setReceiving(true)}
            disabled={saveMutation.isPending}
            className="rounded border border-slate-300 px-4 py-2 disabled:opacity-50"
          >
            Record a delivery
          </button>
        )}
        {purchase.status !== "ordered" && (
          <button onClick={() => unreceiveMutation.mutate()} className="rounded border border-slate-300 px-4 py-2">
            Undo all deliveries
          </button>
        )}
        <button onClick={() => deleteMutation.mutate()} className="rounded border border-red-300 px-4 py-2 text-red-600">
          Delete purchase
        </button>
      </div>
      <ErrorBanner
        error={
          saveMutation.error ??
          unreceiveMutation.error ??
          undoBatchMutation.error ??
          undoReceiptMutation.error ??
          closeLineMutation.error ??
          reopenLineMutation.error ??
          deleteMutation.error
        }
      />

      {/* The billing question is a checkbox inside the confirmation rather than the choice
          between its two buttons: Escape and a backdrop click both mean cancel, and closing
          a line short is not something either gesture should be able to do by accident. */}
      <ConfirmDialog
        open={closingLineId !== null}
        title="Close this line short?"
        tone="default"
        body={
          <div className="flex flex-col gap-3">
            <p>
              Nothing more is expected on this line, so it stops counting as on order and the
              purchase can complete. What was ordered stays on the record.
            </p>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={closeApportion}
                onChange={(e) => setCloseApportion(e.target.checked)}
              />
              <span>
                The supplier charged for the full quantity anyway — put the whole line cost on what
                actually arrived, rather than only its share.
              </span>
            </label>
          </div>
        }
        confirmLabel="Close line"
        busy={closeLineMutation.isPending}
        onConfirm={() => {
          closeLineMutation.mutate({ lineId: closingLineId!, apportion: closeApportion });
          setClosingLineId(null);
        }}
        onCancel={() => setClosingLineId(null)}
      />

      {receiving && (
        <ReceiveDialog
          purchase={purchase}
          materials={materials ?? []}
          busy={receiptsMutation.isPending}
          error={receiptsMutation.error}
          onSubmit={(receivedAt, receiptLines) => receiptsMutation.mutate({ receivedAt, lines: receiptLines })}
          onClose={() => setReceiving(false)}
        />
      )}
    </div>
  );
}
