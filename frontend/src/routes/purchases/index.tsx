import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { purchasesApi } from "../../api/purchases";
import type { Purchase } from "../../api/types";
import { ErrorBanner } from "../../components/common/ErrorBanner";

export const Route = createFileRoute("/purchases/")({
  component: PurchasesList,
});

function lineTotal(purchase: Purchase): number {
  return purchase.lines.reduce((sum, l) => sum + Number(l.total_cost), 0);
}

function PurchasesList() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["purchases"], queryFn: () => purchasesApi.list() });

  const receiveMutation = useMutation({
    mutationFn: (id: number) => purchasesApi.receive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
    },
  });

  const unreceiveMutation = useMutation({
    mutationFn: (id: number) => purchasesApi.unreceive(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      queryClient.invalidateQueries({ queryKey: ["materials"] });
    },
  });

  if (isLoading) return <p>Loading purchases…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Purchases</h1>
        <Link to="/purchases/new" className="rounded bg-slate-900 px-4 py-2 text-white">
          New purchase
        </Link>
      </div>

      <ErrorBanner error={receiveMutation.error ?? unreceiveMutation.error} />

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Supplier</th>
            <th className="p-2">Order date</th>
            <th className="p-2">Status</th>
            <th className="p-2">Lines</th>
            <th className="p-2">Total cost</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {data?.map((purchase) => (
            <tr key={purchase.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="p-2">
                <Link to="/purchases/$purchaseId" params={{ purchaseId: String(purchase.id) }} className="text-slate-900 underline">
                  {purchase.supplier_name ?? "—"}
                </Link>
              </td>
              <td className="p-2">{purchase.order_date}</td>
              <td className="p-2">
                <span
                  className={`rounded px-2 py-0.5 text-xs ${
                    purchase.status === "received" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {purchase.status === "received" ? "Received" : "Ordered"}
                </span>
              </td>
              <td className="p-2">{purchase.lines.length}</td>
              <td className="p-2">£{lineTotal(purchase).toFixed(2)}</td>
              <td className="p-2">
                {purchase.status === "ordered" ? (
                  <button
                    onClick={() => receiveMutation.mutate(purchase.id)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs"
                  >
                    Receive
                  </button>
                ) : (
                  <button
                    onClick={() => unreceiveMutation.mutate(purchase.id)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs"
                  >
                    Un-receive
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
