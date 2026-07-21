import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import { purchasesApi, type PurchaseLineInput } from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { PurchaseLineEditor } from "../../components/purchases/PurchaseLineEditor";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";

export const Route = createFileRoute("/purchases/new")({
  component: NewPurchase,
});

function NewPurchase() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: suppliers } = useQuery({ queryKey: ["suppliers"], queryFn: suppliersApi.list });

  const [supplier, setSupplier] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [orderDate, setOrderDate] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([]);

  const createMutation = useMutation({
    mutationFn: async () => {
      let resolvedSupplierId = supplierId;
      if (!resolvedSupplierId && supplier.trim()) {
        resolvedSupplierId = (await suppliersApi.findOrCreate(supplier.trim())).id;
      }
      return purchasesApi.create({
        supplier_id: resolvedSupplierId,
        order_date: orderDate || null,
        notes: notes || null,
        lines,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      navigate({ to: "/purchases" });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">New purchase</h1>

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

      <div>
        <button
          onClick={() => createMutation.mutate()}
          disabled={lines.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Save purchase
        </button>
      </div>
      <ErrorBanner error={createMutation.error} />
    </div>
  );
}
