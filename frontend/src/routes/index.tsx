import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { dashboardApi } from "../api/dashboard";
import { materialsApi } from "../api/materials";
import type { LowStockMaterial } from "../api/types";
import { ErrorBanner } from "../components/common/ErrorBanner";
import { roundQty } from "../lib/format";

function groupBySupplier(materials: LowStockMaterial[]): { supplierName: string; materials: LowStockMaterial[] }[] {
  const groups: { supplierName: string; materials: LowStockMaterial[] }[] = [];
  for (const m of materials) {
    const supplierName = m.supplier_name ?? "No supplier";
    const last = groups[groups.length - 1];
    if (last && last.supplierName === supplierName) {
      last.materials.push(m);
    } else {
      groups.push({ supplierName, materials: [m] });
    }
  }
  return groups;
}

const STATUS_STYLES: Record<LowStockMaterial["status"], string> = {
  critical: "bg-red-100 text-red-800",
  warning: "bg-amber-100 text-amber-800",
  insufficient_data: "bg-slate-100 text-slate-600",
};

const STATUS_LABELS: Record<LowStockMaterial["status"], string> = {
  critical: "Critical",
  warning: "Warning",
  insufficient_data: "Not enough history",
};

function formatWeeksOfSupply(m: LowStockMaterial): string {
  if (m.weeks_of_supply == null) return "—";
  const weeks = Number(m.weeks_of_supply);
  const fgWeeks = m.fg_buffer_weeks != null ? Number(m.fg_buffer_weeks) : 0;
  if (fgWeeks > 0.05) {
    return `${weeks.toFixed(1)} wks (incl. ${fgWeeks.toFixed(1)} wk from finished-goods stock)`;
  }
  return `${weeks.toFixed(1)} wks`;
}

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
        <SummaryCard label="Materials needing attention" value={String(data.low_stock_materials.length)} />
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

      {data.items_due_for_count.length > 0 && (
        <section>
          <h2 className="mb-2 text-lg font-semibold">Due for counting</h2>
          <p className="mb-2 text-sm text-slate-500">
            Items whose count cadence has come round. Nothing here blocks any other work — it's a list of what
            to check next time you do a stock take.
          </p>
          <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="p-2">Item</th>
                <th className="p-2">Tier</th>
                <th className="p-2">Every</th>
                <th className="p-2">Last counted</th>
                <th className="p-2">Overdue by</th>
              </tr>
            </thead>
            <tbody>
              {data.items_due_for_count.map((item) => (
                <tr
                  key={`${item.scope}-${item.material_id ?? item.product_id}-${item.variant_id ?? "base"}`}
                  className="border-b border-slate-100"
                >
                  <td className="p-2">
                    {item.scope === "material" ? (
                      <Link
                        to="/materials/$materialId"
                        params={{ materialId: String(item.material_id) }}
                        className="text-slate-900 underline"
                      >
                        {item.name}
                      </Link>
                    ) : (
                      <Link
                        to="/products/$productId"
                        params={{ productId: String(item.product_id) }}
                        className="text-slate-900 underline"
                      >
                        {item.name}
                      </Link>
                    )}
                  </td>
                  <td className="p-2">{item.abc_class}</td>
                  <td className="p-2">{item.interval_days} days</td>
                  <td className="p-2">
                    {item.last_stock_take_at ? new Date(item.last_stock_take_at).toLocaleDateString() : "Never"}
                  </td>
                  {/* Never-counted has no overdue figure to show — there is no date to measure
                      from, and a made-up number would rank it against genuinely overdue items
                      on a scale it isn't on. */}
                  <td className="p-2">
                    {item.days_overdue === null ? (
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Never counted</span>
                    ) : item.days_overdue === 0 ? (
                      "Due today"
                    ) : (
                      <span className="text-amber-800">{item.days_overdue} days</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.items_due_for_count_total > data.items_due_for_count.length && (
            <p className="mt-1 text-sm text-slate-500">
              Showing {data.items_due_for_count.length} of {data.items_due_for_count_total} items due.
            </p>
          )}
        </section>
      )}

      <section>
        <h2 className="mb-2 text-lg font-semibold">Materials — time to stockout</h2>
        <p className="mb-2 text-sm text-slate-500">
          Weeks until you can't fulfil demand for this material — includes finished-goods stock still covering
          sales before any new builds draw on it.
        </p>
        {data.low_stock_materials.length === 0 ? (
          <p className="text-slate-500">Nothing below its warning threshold.</p>
        ) : (
          groupBySupplier(data.low_stock_materials).map((group) => (
            <div key={group.supplierName} className="mb-4">
              <h3 className="mb-1 text-sm font-semibold text-slate-600">{group.supplierName}</h3>
              <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
                <thead>
                  <tr className="border-b border-slate-200">
                    <th className="p-2">Material</th>
                    <th className="p-2">Status</th>
                    <th className="p-2">Time to stockout</th>
                    <th className="p-2">Consumption rate</th>
                    <th className="p-2">On hand</th>
                    <th className="p-2">On order</th>
                    <th className="p-2" />
                  </tr>
                </thead>
                <tbody>
                  {group.materials.map((m) => (
                    <tr key={m.id} className="border-b border-slate-100">
                      <td className="p-2">{m.name}</td>
                      <td className="p-2">
                        <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLES[m.status]}`}>
                          {STATUS_LABELS[m.status]}
                        </span>
                      </td>
                      <td className="p-2">{formatWeeksOfSupply(m)}</td>
                      <td className="p-2">
                        {m.consumption_rate_per_week != null ? `${roundQty(m.consumption_rate_per_week)}/wk` : "—"}
                      </td>
                      <td className="p-2 text-red-600">{roundQty(m.current_qty)}</td>
                      <td className="p-2">{Number(m.on_order_qty) > 0 ? roundQty(m.on_order_qty) : "—"}</td>
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
            </div>
          ))
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
