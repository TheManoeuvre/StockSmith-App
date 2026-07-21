import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { materialsApi } from "../../api/materials";
import { purchasesApi, type PurchaseLineInput } from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { PurchaseLineEditor } from "../../components/purchases/PurchaseLineEditor";
import { ErrorBanner } from "../../components/common/ErrorBanner";
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
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([]);

  useEffect(() => {
    if (purchase) {
      setSupplier(purchase.supplier_name ?? "");
      setSupplierId(purchase.supplier_id);
      setOrderDate(purchase.order_date);
      setNotes(purchase.notes ?? "");
      setLines(purchase.lines.map((l) => ({ material_id: l.material_id, qty: l.qty, total_cost: l.total_cost, notes: l.notes })));
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
      await purchasesApi.update(id, { supplier_id: resolvedSupplierId, order_date: orderDate || null, notes: notes || null });
      await purchasesApi.replaceLines(id, lines);
    },
    onSuccess: invalidate,
  });

  const receiveMutation = useMutation({ mutationFn: () => purchasesApi.receive(id), onSuccess: invalidate });
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
        <span
          className={`rounded px-2 py-0.5 text-xs ${
            purchase.status === "received" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
          }`}
        >
          {purchase.status === "received" ? "Received" : "Ordered"}
        </span>
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
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Notes</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>

      <PurchaseLineEditor materials={materials ?? []} lines={lines} onChange={setLines} />

      <div className="flex gap-2">
        <button
          onClick={() => saveMutation.mutate()}
          disabled={lines.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Save changes
        </button>
        {purchase.status === "ordered" ? (
          <button onClick={() => receiveMutation.mutate()} className="rounded border border-slate-300 px-4 py-2">
            Receive
          </button>
        ) : (
          <button onClick={() => unreceiveMutation.mutate()} className="rounded border border-slate-300 px-4 py-2">
            Un-receive
          </button>
        )}
        <button onClick={() => deleteMutation.mutate()} className="rounded border border-red-300 px-4 py-2 text-red-600">
          Delete purchase
        </button>
      </div>
      <ErrorBanner
        error={saveMutation.error ?? receiveMutation.error ?? unreceiveMutation.error ?? deleteMutation.error}
      />
    </div>
  );
}
