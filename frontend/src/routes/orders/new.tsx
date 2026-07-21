import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { productsApi } from "../../api/products";
import { ordersApi, type OrderLineInput } from "../../api/orders";
import { OrderLineEditor } from "../../components/orders/OrderLineEditor";
import { ErrorBanner } from "../../components/common/ErrorBanner";

export const Route = createFileRoute("/orders/new")({
  component: NewOrder,
});

function NewOrder() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: products } = useQuery({ queryKey: ["products"], queryFn: productsApi.list });

  const [buyerName, setBuyerName] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<OrderLineInput[]>([]);

  const createMutation = useMutation({
    mutationFn: () =>
      ordersApi.create({
        buyer_name: buyerName || null,
        notes: notes || null,
        lines,
      }),
    onSuccess: (order) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      navigate({ to: "/orders/$orderId", params: { orderId: String(order.id) } });
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-xl font-semibold">New order</h1>

      <div className="flex flex-wrap gap-4 rounded bg-white p-4 shadow-sm">
        <label className="flex flex-col gap-1">
          <span className="text-sm">Buyer name</span>
          <input
            className="rounded border border-slate-300 px-2 py-1"
            value={buyerName}
            onChange={(e) => setBuyerName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 flex-1">
          <span className="text-sm">Notes</span>
          <input className="rounded border border-slate-300 px-2 py-1" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>

      <OrderLineEditor products={products ?? []} lines={lines} onChange={setLines} />

      <div>
        <button
          onClick={() => createMutation.mutate()}
          disabled={lines.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-50"
        >
          Save order
        </button>
      </div>
      <ErrorBanner error={createMutation.error} />
    </div>
  );
}
