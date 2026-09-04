import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialsApi } from "../../api/materials";
import { purchasesApi, type PurchaseLineInput } from "../../api/purchases";
import { suppliersApi } from "../../api/suppliers";
import { PurchaseLineEditor } from "../../components/purchases/PurchaseLineEditor";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { FieldRow } from "../../components/common/FieldRow";

export const Route = createFileRoute("/purchases/new")({
  component: NewPurchase,
});

function NewPurchase() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
  });
  const { data: suppliers } = useQuery({
    queryKey: ["suppliers"],
    queryFn: suppliersApi.list,
  });

  const [supplier, setSupplier] = useState("");
  const [supplierId, setSupplierId] = useState<number | null>(null);
  const [supplierOrderNumber, setSupplierOrderNumber] = useState("");
  const [orderDate, setOrderDate] = useState("");
  const [expectedArrivalDate, setExpectedArrivalDate] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<PurchaseLineInput[]>([]);

  const createMutation = useMutation({
    mutationFn: async () => {
      let resolvedSupplierId = supplierId;
      if (!resolvedSupplierId && supplier.trim()) {
        resolvedSupplierId = (await suppliersApi.findOrCreate(supplier.trim()))
          .id;
      }
      return purchasesApi.create({
        supplier_id: resolvedSupplierId,
        supplier_order_number: supplierOrderNumber.trim() || null,
        order_date: orderDate || null,
        expected_arrival_date: expectedArrivalDate || null,
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
    <DetailPanel
      title="New purchase"
      onClose={() => navigate({ to: "/purchases" })}
    >
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm">
          <FieldRow label="Supplier">
            <CreatableSelect
              className="rounded border border-slate-300 px-2 py-1"
              options={suppliers ?? []}
              value={supplier}
              onChange={setSupplier}
              onResolved={setSupplierId}
            />
          </FieldRow>
          <FieldRow label="Supplier order #">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={supplierOrderNumber}
              onChange={(e) => setSupplierOrderNumber(e.target.value)}
              placeholder="Their PO / order number"
            />
          </FieldRow>
          <FieldRow label="Order date">
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={orderDate}
              onChange={(e) => setOrderDate(e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Expected arrival">
            <input
              type="date"
              className="rounded border border-slate-300 px-2 py-1"
              value={expectedArrivalDate}
              onChange={(e) => setExpectedArrivalDate(e.target.value)}
            />
          </FieldRow>
          <FieldRow label="Notes">
            <input
              className="w-full rounded border border-slate-300 px-2 py-1"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </FieldRow>
        </div>

        <PurchaseLineEditor
          materials={materials ?? []}
          lines={lines}
          onChange={setLines}
        />

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
    </DetailPanel>
  );
}
