import {
  createFileRoute,
  Link,
  Outlet,
  useNavigate,
} from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type MouseEvent } from "react";
import { productCategoriesApi } from "../../api/productCategories";
import { stockTakesApi } from "../../api/stockTakes";
import type { StockTakeScope } from "../../api/types";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { Badge } from "../../components/common/Badge";
import { FilterTabs } from "../../components/common/FilterTabs";
import { Th } from "../../components/common/ListTable";
import { formatDayMonth } from "../../lib/format";

/**
 * Pathless layout for /stock-takes: the list lives here (not in index.tsx) so it stays
 * mounted and visible while `$stockTakeId` renders into the `<Outlet>` as a slide-over panel
 * on top of it — see components/common/DetailPanel.tsx. `unresolved` (a cross-cutting list,
 * not a per-take detail) stays a plain page rendered into the same Outlet, without the panel
 * chrome. index.tsx is a trivial route now; this component is what actually renders the list.
 */
export const Route = createFileRoute("/stock-takes")({
  component: StockTakesLayout,
  // Preselects the scope picker with "due for counting only", so the dashboard's due list
  // can link straight into starting a take for exactly those items.
  validateSearch: (search: Record<string, unknown>): { overdue?: boolean } =>
    search.overdue ? { overdue: true } : {},
});

function StockTakesLayout() {
  return (
    <>
      <StockTakesListContent />
      <Outlet />
    </>
  );
}

function StockTakesListContent() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const overdueFromUrl = Route.useSearch().overdue ?? false;

  const {
    data: takes,
    isLoading,
    error,
  } = useQuery({ queryKey: ["stock-takes"], queryFn: stockTakesApi.list });
  const { data: productCategories } = useQuery({
    queryKey: ["product-categories"],
    queryFn: productCategoriesApi.list,
  });

  const [showPicker, setShowPicker] = useState(overdueFromUrl);
  const [tab, setTab] = useState<"all" | "open" | "closed">("all");
  const [includeMaterials, setIncludeMaterials] = useState(true);
  const [includeProducts, setIncludeProducts] = useState(true);
  const [categoryIds, setCategoryIds] = useState<Set<number>>(new Set());
  const { categories } = useMaterialCategories();
  const [typeIds, setTypeIds] = useState<Set<number>>(new Set());
  const [overdueOnly, setOverdueOnly] = useState(overdueFromUrl);

  const scope: StockTakeScope = useMemo(
    () => ({
      include_materials: includeMaterials,
      include_products: includeProducts,
      material_category_ids: [...categoryIds],
      product_category_ids: [...typeIds],
      overdue_only: overdueOnly,
    }),
    [includeMaterials, includeProducts, categoryIds, typeIds, overdueOnly],
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
      navigate({
        to: "/stock-takes/$stockTakeId",
        params: { stockTakeId: String(created.stock_take.id) },
      });
    },
  });

  const toggle = <T,>(set: Set<T>, value: T): Set<T> => {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    return next;
  };

  if (isLoading) return <p>Loading stock takes…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  const all = takes ?? [];
  const openN = all.filter((t) => t.status === "open").length;
  const countFor = (id: "all" | "open" | "closed") =>
    id === "all" ? all.length : all.filter((t) => t.status === id).length;
  const rows = tab === "all" ? all : all.filter((t) => t.status === tab);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Stock takes</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {openN} open · {all.length - openN} closed
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to="/stock-takes/unresolved"
            className="rounded border border-slate-300 px-3 py-2 text-sm"
          >
            Unresolved variances
          </Link>
          <button
            onClick={() => setShowPicker((v) => !v)}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
          >
            {showPicker ? "Cancel" : "Start stock take"}
          </button>
        </div>
      </div>

      <FilterTabs
        tabs={[
          { id: "all", label: "All takes", count: countFor("all") },
          { id: "open", label: "Open", count: countFor("open") },
          { id: "closed", label: "Closed", count: countFor("closed") },
        ]}
        active={tab}
        onChange={(id) => setTab(id as "all" | "open" | "closed")}
      />

      {showPicker && (
        <div className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm">
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeMaterials}
                onChange={() => setIncludeMaterials((v) => !v)}
              />
              Materials
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeProducts}
                onChange={() => setIncludeProducts((v) => !v)}
              />
              Finished stock
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={overdueOnly}
                onChange={() => setOverdueOnly((v) => !v)}
              />
              Only what's due for counting
            </label>
          </div>

          {includeMaterials && (
            <div>
              <p className="mb-1 text-sm">
                Material categories (all if none ticked)
              </p>
              <div className="flex flex-wrap gap-3 text-sm">
                {categories.map((c) => (
                  <label key={c.id} className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={categoryIds.has(c.id)}
                      onChange={() => setCategoryIds(toggle(categoryIds, c.id))}
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            </div>
          )}

          {includeProducts &&
            productCategories &&
            productCategories.length > 0 && (
              <div>
                <p className="mb-1 text-sm">
                  Product categories (all if none ticked)
                </p>
                <div className="flex flex-wrap gap-3 text-sm">
                  {productCategories.map((t) => (
                    <label key={t.id} className="flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={typeIds.has(t.id)}
                        onChange={() => setTypeIds(toggle(typeIds, t.id))}
                      />
                      {t.name}
                    </label>
                  ))}
                </div>
              </div>
            )}

          {preview && (
            <div className="text-sm text-slate-600">
              <p>
                <strong>{preview.candidate_count}</strong> item
                {preview.candidate_count === 1 ? "" : "s"} to count
                {preview.material_count > 0 && preview.product_count > 0 && (
                  <>
                    {" "}
                    — {preview.material_count} material
                    {preview.material_count === 1 ? "" : "s"},{" "}
                    {preview.product_count} finished
                  </>
                )}
                . {preview.scope_description}.
              </p>
              {/* A soft lock: said out loud, never enforced. Counting one item on two takes
                  is odd but legitimate, and blocking it would strand someone behind a take
                  they forgot to close. */}
              {preview.warnings.length > 0 && (
                <div className="mt-2 rounded border border-amber-300 bg-amber-50 p-3 text-amber-800">
                  <p className="font-medium">
                    {preview.warnings.length} item
                    {preview.warnings.length === 1 ? " is" : "s are"} already on
                    an open stock take
                  </p>
                  <ul className="mt-1 list-disc pl-5">
                    {preview.warnings.slice(0, 5).map((w, i) => (
                      <li key={i}>
                        {w.name} — take #{w.other_stock_take_id}, started{" "}
                        {new Date(w.other_started_at).toLocaleDateString()}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1">
                    You can carry on; both takes will keep their own counts.
                  </p>
                </div>
              )}
            </div>
          )}

          <div>
            <button
              onClick={() => createMutation.mutate()}
              disabled={
                createMutation.isPending ||
                !preview ||
                preview.candidate_count === 0
              }
              className="rounded bg-slate-900 px-4 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Start counting{" "}
              {preview
                ? `${preview.candidate_count} item${preview.candidate_count === 1 ? "" : "s"}`
                : ""}
            </button>
          </div>
          <ErrorBanner error={createMutation.error} />
        </div>
      )}

      {all.length === 0 ? (
        <p className="text-slate-500">No stock takes yet.</p>
      ) : (
        <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/60">
              <Th>Started</Th>
              <Th>Scope</Th>
              <Th>Status</Th>
              <Th>Progress</Th>
              <Th>Flagged</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => {
              const openDetail = (e: MouseEvent<HTMLTableRowElement>) => {
                if (
                  (e.target as HTMLElement).closest("a, button, input, select, label")
                )
                  return;
                navigate({
                  to: "/stock-takes/$stockTakeId",
                  params: { stockTakeId: String(t.id) },
                });
              };
              return (
                <tr
                  key={t.id}
                  onClick={openDetail}
                  className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50"
                >
                  <td className="p-2">
                    <Link
                      to="/stock-takes/$stockTakeId"
                      params={{ stockTakeId: String(t.id) }}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {formatDayMonth(t.started_at)}
                    </Link>
                  </td>
                  <td className="p-2 text-slate-600">{t.scope_description}</td>
                  <td className="p-2">
                    {t.status === "open" ? (
                      <Badge className="bg-amber-100 text-amber-800">
                        Open {t.open_days} day{t.open_days === 1 ? "" : "s"}
                      </Badge>
                    ) : (
                      <Badge className="bg-slate-100 text-slate-600">Closed</Badge>
                    )}
                  </td>
                  <td className="p-2 tabular-nums">
                    {t.counted_count} / {t.line_count}
                  </td>
                  <td className="p-2 tabular-nums">
                    {t.conflict_count > 0 ? (
                      <span className="font-medium text-amber-700">
                        {t.conflict_count}
                      </span>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
