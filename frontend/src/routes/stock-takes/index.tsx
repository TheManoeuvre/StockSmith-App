import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { productTypesApi } from "../../api/productTypes";
import { stockTakesApi } from "../../api/stockTakes";
import type { MaterialCategory, StockTakeScope } from "../../api/types";
import { ErrorBanner } from "../../components/common/ErrorBanner";

export const Route = createFileRoute("/stock-takes/")({
  component: StockTakesList,
  // Preselects the scope picker with "due for counting only", so the dashboard's due list
  // can link straight into starting a take for exactly those items.
  validateSearch: (search: Record<string, unknown>): { overdue?: boolean } =>
    search.overdue ? { overdue: true } : {},
});

const CATEGORIES: MaterialCategory[] = ["filament", "resin", "pigment", "hardware", "packaging", "blanks", "other"];

function StockTakesList() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const overdueFromUrl = Route.useSearch().overdue ?? false;

  const { data: takes, isLoading, error } = useQuery({ queryKey: ["stock-takes"], queryFn: stockTakesApi.list });
  const { data: productTypes } = useQuery({ queryKey: ["product-types"], queryFn: productTypesApi.list });

  const [showPicker, setShowPicker] = useState(overdueFromUrl);
  const [includeMaterials, setIncludeMaterials] = useState(true);
  const [includeProducts, setIncludeProducts] = useState(true);
  const [categories, setCategories] = useState<Set<MaterialCategory>>(new Set());
  const [typeIds, setTypeIds] = useState<Set<number>>(new Set());
  const [overdueOnly, setOverdueOnly] = useState(overdueFromUrl);

  const scope: StockTakeScope = useMemo(
    () => ({
      include_materials: includeMaterials,
      include_products: includeProducts,
      material_categories: [...categories],
      product_type_ids: [...typeIds],
      overdue_only: overdueOnly,
    }),
    [includeMaterials, includeProducts, categories, typeIds, overdueOnly],
  );

  // Live so the count updates as the scope is edited — starting a take you then have to
  // abandon because it caught the wrong things is the thing worth avoiding.
  const { data: preview } = useQuery({
    queryKey: ["stock-take-preview", scope],
    queryFn: () => stockTakesApi.previewScope(scope),
    enabled: showPicker && (includeMaterials || includeProducts),
  });

  const createMutation = useMutation({
    mutationFn: () => stockTakesApi.create(scope),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["stock-takes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      navigate({ to: "/stock-takes/$stockTakeId", params: { stockTakeId: String(created.stock_take.id) } });
    },
  });

  const toggle = <T,>(set: Set<T>, value: T): Set<T> => {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    return next;
  };

  if (isLoading) return <p>Loading stock takes…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Stock takes</h1>
        <div className="flex gap-2">
          <Link to="/stock-takes/unresolved" className="rounded border border-slate-300 px-3 py-2 text-sm">
            Unresolved variances
          </Link>
          <button onClick={() => setShowPicker((v) => !v)} className="rounded bg-slate-900 px-4 py-2 text-white">
            {showPicker ? "Cancel" : "Start stock take"}
          </button>
        </div>
      </div>

      {showPicker && (
        <div className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm">
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={includeMaterials} onChange={() => setIncludeMaterials((v) => !v)} />
              Materials
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={includeProducts} onChange={() => setIncludeProducts((v) => !v)} />
              Finished stock
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={overdueOnly} onChange={() => setOverdueOnly((v) => !v)} />
              Only what's due for counting
            </label>
          </div>

          {includeMaterials && (
            <div>
              <p className="mb-1 text-sm">Material categories (all if none ticked)</p>
              <div className="flex flex-wrap gap-3 text-sm">
                {CATEGORIES.map((c) => (
                  <label key={c} className="flex items-center gap-1">
                    <input type="checkbox" checked={categories.has(c)} onChange={() => setCategories(toggle(categories, c))} />
                    {c}
                  </label>
                ))}
              </div>
            </div>
          )}

          {includeProducts && productTypes && productTypes.length > 0 && (
            <div>
              <p className="mb-1 text-sm">Product types (all if none ticked)</p>
              <div className="flex flex-wrap gap-3 text-sm">
                {productTypes.map((t) => (
                  <label key={t.id} className="flex items-center gap-1">
                    <input type="checkbox" checked={typeIds.has(t.id)} onChange={() => setTypeIds(toggle(typeIds, t.id))} />
                    {t.name}
                  </label>
                ))}
              </div>
            </div>
          )}

          {preview && (
            <div className="text-sm text-slate-600">
              <p>
                <strong>{preview.candidate_count}</strong> item{preview.candidate_count === 1 ? "" : "s"} to count
                {preview.material_count > 0 && preview.product_count > 0 && (
                  <> — {preview.material_count} material{preview.material_count === 1 ? "" : "s"}, {preview.product_count} finished</>
                )}
                . {preview.scope_description}.
              </p>
              {/* A soft lock: said out loud, never enforced. Counting one item on two takes
                  is odd but legitimate, and blocking it would strand someone behind a take
                  they forgot to close. */}
              {preview.warnings.length > 0 && (
                <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-3 text-amber-800">
                  <p className="font-medium">
                    {preview.warnings.length} item{preview.warnings.length === 1 ? " is" : "s are"} already on an open
                    stock take
                  </p>
                  <ul className="mt-1 list-disc pl-5">
                    {preview.warnings.slice(0, 5).map((w, i) => (
                      <li key={i}>
                        {w.name} — take #{w.other_stock_take_id}, started{" "}
                        {new Date(w.other_started_at).toLocaleDateString()}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1">You can carry on; both takes will keep their own counts.</p>
                </div>
              )}
            </div>
          )}

          <div>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !preview || preview.candidate_count === 0}
              className="rounded bg-slate-900 px-4 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Start counting {preview ? `${preview.candidate_count} item${preview.candidate_count === 1 ? "" : "s"}` : ""}
            </button>
          </div>
          <ErrorBanner error={createMutation.error} />
        </div>
      )}

      {takes && takes.length === 0 ? (
        <p className="text-slate-500">No stock takes yet.</p>
      ) : (
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Started</th>
              <th className="p-2">Scope</th>
              <th className="p-2">Status</th>
              <th className="p-2">Progress</th>
              <th className="p-2">Flagged</th>
            </tr>
          </thead>
          <tbody>
            {takes?.map((t) => (
              <tr key={t.id} className="border-b border-slate-100 hover:bg-slate-50">
                <td className="p-2">
                  <Link
                    to="/stock-takes/$stockTakeId"
                    params={{ stockTakeId: String(t.id) }}
                    className="text-slate-900 underline"
                  >
                    {new Date(t.started_at).toLocaleDateString()}
                  </Link>
                </td>
                <td className="p-2">{t.scope_description}</td>
                <td className="p-2">
                  {t.status === "open" ? (
                    <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                      Open {t.open_days} day{t.open_days === 1 ? "" : "s"}
                    </span>
                  ) : (
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">Closed</span>
                  )}
                </td>
                <td className="p-2">
                  {t.counted_count} of {t.line_count} counted
                </td>
                <td className="p-2">
                  {t.conflict_count > 0 ? <span className="text-amber-800">{t.conflict_count}</span> : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
