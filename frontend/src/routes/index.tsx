import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dashboardApi } from "../api/dashboard";
import { materialsApi } from "../api/materials";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { roundQty } from "../lib/format";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: dashboardApi.summary,
  });

  const draftPurchaseMutation = useMutation({
    mutationFn: (materialId: number) => materialsApi.createDraftPurchase(materialId),
    onSuccess: (purchase) => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      navigate({ to: "/purchases/$purchaseId", params: { purchaseId: String(purchase.id) } });
    },
  });

  if (isLoading) return <p>Loading dashboard…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;
  if (!data) return null;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <SummaryCard label="Total inventory value" value={`£${Number(data.total_inventory_value).toFixed(2)}`} />
        <SummaryCard label="Active products" value={String(data.active_product_count)} />
        <SummaryCard label="Materials below reorder threshold" value={String(data.low_stock_materials.length)} />
      </div>

      {data.orders_awaiting_inventory.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-semibold">Orders awaiting inventory</h2>
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Product</th>
                <th className="p-2">Short by</th>
                <th className="p-2">Order placed</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {data.orders_awaiting_inventory.map((o) => (
                <tr key={o.line_id} className="border-b border-slate-100">
                  <td className="p-2">
                    <Link
                      to="/orders/$orderId"
                      params={{ orderId: String(o.order_id) }}
                      className="text-slate-900 underline"
                    >
                      {o.product_name ?? "—"}
                      {o.variant_name ? ` — ${o.variant_name}` : ""}
                    </Link>
                  </td>
                  <td className="p-2 text-red-600">{o.short_by}</td>
                  <td className="p-2">{new Date(o.order_placed_at).toLocaleDateString()}</td>
                  <td className="p-2">
                    {o.product_id != null && (
                      <Link
                        to="/products/$productId"
                        params={{ productId: String(o.product_id) }}
                        className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                      >
                        Build now
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {data.orders_awaiting_packaging.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-semibold">Orders awaiting packaging</h2>
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Order</th>
                <th className="p-2">Material</th>
                <th className="p-2">Short by</th>
                <th className="p-2">Order placed</th>
              </tr>
            </thead>
            <tbody>
              {data.orders_awaiting_packaging.map((o, i) => (
                <tr key={`${o.order_id}-${o.material_id}-${i}`} className="border-b border-slate-100">
                  <td className="p-2">
                    <Link
                      to="/orders/$orderId"
                      params={{ orderId: String(o.order_id) }}
                      className="text-slate-900 underline"
                    >
                      Order #{o.order_id}
                    </Link>
                  </td>
                  <td className="p-2">{o.material_name}</td>
                  <td className="p-2 text-red-600">{roundQty(o.short_by)}</td>
                  <td className="p-2">{new Date(o.order_placed_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-lg font-semibold">Low stock materials</h2>
        {data.low_stock_materials.length === 0 ? (
          <p className="text-slate-500">Nothing below its reorder threshold.</p>
        ) : (
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Material</th>
                <th className="p-2">On hand</th>
                <th className="p-2">On order</th>
                <th className="p-2">Reorder threshold</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {data.low_stock_materials.map((m) => (
                <tr key={m.id} className="border-b border-slate-100">
                  <td className="p-2">{m.name}</td>
                  <td className="p-2 text-red-600">{roundQty(m.current_qty)}</td>
                  <td className="p-2">{Number(m.on_order_qty) > 0 ? roundQty(m.on_order_qty) : "—"}</td>
                  <td className="p-2">{roundQty(m.reorder_threshold)}</td>
                  <td className="p-2">
                    <button
                      onClick={() => draftPurchaseMutation.mutate(m.id)}
                      className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                    >
                      Create draft purchase
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <ErrorBanner error={draftPurchaseMutation.error} />
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Lowest buildable products</h2>
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Product</th>
              <th className="p-2">Max buildable</th>
              <th className="p-2">Expected max buildable</th>
            </tr>
          </thead>
          <tbody>
            {data.lowest_buildable_products.map((p) => (
              <tr key={p.product_id} className="border-b border-slate-100">
                <td className="p-2">{p.name}</td>
                <td className="p-2">{p.max_buildable ?? "No BOM set"}</td>
                <td className="p-2">{p.expected_max_buildable ?? "No BOM set"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {data.margin_alerts.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-semibold">Products with significant margin changes</h2>
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Product</th>
                <th className="p-2">Previous margin</th>
                <th className="p-2">Current margin</th>
              </tr>
            </thead>
            <tbody>
              {data.margin_alerts.map((a) => {
                const diff = Number(a.current_margin_percent) - Number(a.previous_margin_percent);
                return (
                  <tr key={a.product_id} className="border-b border-slate-100">
                    <td className="p-2">{a.name}</td>
                    <td className="p-2">{Number(a.previous_margin_percent).toFixed(1)}%</td>
                    <td className={`p-2 ${diff < 0 ? "text-red-600" : "text-green-700"}`}>
                      {Number(a.current_margin_percent).toFixed(1)}% ({diff > 0 ? "+" : ""}
                      {diff.toFixed(1)} pts)
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white p-4 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  );
}
