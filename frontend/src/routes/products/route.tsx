import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { platformsApi, type ProductSyncStatus } from "../../api/platforms";
import { productsApi } from "../../api/products";
import type { ListingPlatform, Product } from "../../api/types";
import { CONNECTABLE_PLATFORMS } from "../../lib/platforms";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import { useDirtyRegistration } from "../../hooks/useDirtyRegistry";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { Badge } from "../../components/common/Badge";
import { CopyButton } from "../../components/common/CopyButton";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { FilterTabs } from "../../components/common/FilterTabs";
import { GroupHeaderRow, Th } from "../../components/common/ListTable";
import { PlatformSyncBadge } from "../../components/products/PlatformSyncBadge";
import { sellableSummary } from "../../lib/format";
import { formatUnitCost } from "../../lib/money";

/**
 * Pathless layout for /products: the list lives here (not in index.tsx) so it stays mounted
 * and visible while `$productId` renders into the `<Outlet>` as a slide-over panel on top of
 * it — see components/common/DetailPanel.tsx. index.tsx is a trivial route now; this
 * component is what actually renders the list.
 */
export const Route = createFileRoute("/products")({
  component: ProductsLayout,
});

export const PRODUCTS_PAGE_SIZE = 50;

/**
 * The page's products, split into runs of one category.
 *
 * The order is the server's — it sorts by category then name so that pagination cuts
 * through the grouping rather than scattering one category over every page — so this only
 * walks the page and notices where the heading changes. Same shape as the materials list,
 * which is the point: the two read alike now that products have a category too.
 */
function groupByCategory(
  products: Product[],
): { key: string; label: string; products: Product[] }[] {
  const out: { key: string; label: string; products: Product[] }[] = [];
  for (const product of products) {
    const label = product.product_category_name ?? "Uncategorised";
    const last = out[out.length - 1];
    if (last && last.label === label) {
      last.products.push(product);
      continue;
    }
    out.push({ key: label, label, products: [product] });
  }
  return out;
}

/**
 * One labelled line of the Costs cell.
 *
 * A range collapses to a single figure when its ends match, so a product whose variants all
 * cost the same doesn't read as "£0.59 – £0.59". Where there's no range at all — a product
 * with no variants — the base figure is the whole story and is shown alone.
 *
 * `missingLabel` is the difference between a gap and a legitimate absence: "— no profile"
 * says what to go and fix, whereas a bare dash reads as "nothing to show here". Only the
 * lines where absence is actually a problem pass one.
 */
function CostLine({
  label,
  base,
  min,
  max,
  missingLabel,
}: {
  label: string;
  base: string | null;
  min?: string | null;
  max?: string | null;
  missingLabel?: string;
}) {
  const isRange = min != null && max != null && Number(min) !== Number(max);
  const value = isRange
    ? `${formatUnitCost(min)} – ${formatUnitCost(max)}`
    : min != null
      ? formatUnitCost(min)
      : base != null
        ? formatUnitCost(base)
        : null;
  return (
    <span className="block whitespace-nowrap">
      <span className="inline-block w-8 text-xs text-slate-400">{label}</span>
      {value ?? (
        <span className={missingLabel ? "text-amber-700" : undefined}>
          — {missingLabel ?? ""}
        </span>
      )}
    </span>
  );
}

function ProductsLayout() {
  return (
    <>
      <ProductsListContent />
      <Outlet />
    </>
  );
}

function ProductsListContent() {
  const queryClient = useQueryClient();
  const guard = useGuard();
  const [page, setPage] = useState(0);
  const [tab, setTab] = useState<"all" | "cost-gaps">("all");
  const [searchInput, setSearchInput] = useState("");
  const [q, setQ] = useState("");
  // Collapsed category groups, by label. Session-only, same as the materials list.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  // Debounce the search box into the query key so a keystroke isn't a request. Any change to
  // the effective term (or the tab) sends us back to page 0 — a later page of the wider list
  // is usually past the end of the narrower one.
  useEffect(() => {
    const trimmed = searchInput.trim();
    const t = setTimeout(() => {
      setQ(trimmed);
      setPage(0);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["products", page, tab, q],
    queryFn: () =>
      productsApi.listPaged(PRODUCTS_PAGE_SIZE, page * PRODUCTS_PAGE_SIZE, {
        cogsIncomplete: tab === "cost-gaps",
        q,
      }),
    placeholderData: keepPreviousData,
  });
  const products = data?.items;
  const total = data?.total ?? 0;
  const incompleteTotal = data?.incomplete_total ?? 0;
  // One cheap DB-backed query per connectable platform (no marketplace traffic — this
  // reads stored Listing rows). `retry: false` because a platform that isn't connected
  // 400s, which is an expected steady state here, not a transient failure worth retrying.
  const storeStatusQueries = useQueries({
    queries: CONNECTABLE_PLATFORMS.map((platform) => ({
      queryKey: ["platforms", platform, "all-sync-status"],
      queryFn: () => platformsApi.getAllSyncStatus(platform),
      retry: false,
    })),
  });
  // A platform whose query errored (not connected) is dropped entirely rather than shown
  // as an empty cell, so a shop that only sells on Etsy doesn't get a column of blanks.
  const storeStatuses: {
    platform: ListingPlatform;
    byProduct: Record<number, ProductSyncStatus>;
  }[] = CONNECTABLE_PLATFORMS.flatMap((platform, i) => {
    const result = storeStatusQueries[i];
    return result?.data ? [{ platform, byProduct: result.data }] : [];
  });
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");

  const createFormDirty =
    showForm && (name.trim() !== "" || sku.trim() !== "");
  useDirtyRegistration("new-product", "New product", createFormDirty);

  const createMutation = useMutation({
    mutationFn: () => productsApi.create({ name, sku: sku || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setShowForm(false);
      setName("");
      setSku("");
    },
  });

  const toggleGroup = (label: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const groups = useMemo(() => groupByCategory(products ?? []), [products]);

  if (isLoading) return <p>Loading products…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  // Keep the "Cost gaps" tab visible while it's the active one even if the count just hit
  // zero, so fixing the last gap doesn't yank the view out from under you.
  const showCostGapsTab = incompleteTotal > 0 || tab === "cost-gaps";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Products</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {tab === "cost-gaps"
              ? `${total} with cost gaps`
              : `${total} product${total === 1 ? "" : "s"}${incompleteTotal > 0 ? ` · ${incompleteTotal} with cost gaps` : ""}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CsvImportExport
            onExport={productsApi.exportCsv}
            onImport={productsApi.importCsv}
            invalidateKey={["products", "dashboard-summary"]}
          />
          <button
            onClick={() =>
              guard.attempt(() => setShowForm((v) => !v), {
                prefix: "new-product",
              })
            }
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
          >
            {showForm ? "Cancel" : "Add product"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <FilterTabs
          tabs={[
            {
              id: "all",
              label: "All products",
              count: tab === "all" ? total : undefined,
            },
            ...(showCostGapsTab
              ? [
                  {
                    id: "cost-gaps",
                    label: "Cost gaps",
                    count: incompleteTotal,
                  },
                ]
              : []),
          ]}
          active={tab}
          onChange={(id) => {
            setTab(id as typeof tab);
            setPage(0);
          }}
        />
        <input
          className="w-56 rounded border border-slate-300 px-2.5 py-1.5 text-sm"
          placeholder="Search name, SKU…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      {showForm && (
        <form
          className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-sm">Name</span>
            <input
              required
              className="rounded border border-slate-300 px-2 py-1"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">SKU</span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              placeholder="Auto-generated if left blank"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
            />
          </label>
          <button
            type="submit"
            className="rounded bg-slate-900 px-4 py-1.5 text-white"
          >
            Save
          </button>
          <ErrorBanner error={createMutation.error} />
        </form>
      )}

      <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>{""}</Th>
            <Th>Name</Th>
            <Th>SKU</Th>
            <Th>On hand</Th>
            <Th>Sellable</Th>
            <Th>Sale price</Th>
            {/* The three deductions that make up an order's cost of goods, stacked in one
                column rather than three: at eleven columns wide the table stopped being
                scannable, and these are read together or not at all. */}
            <Th>Costs</Th>
            <Th>Stores</Th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <Fragment key={group.key}>
              <GroupHeaderRow
                label={group.label}
                count={group.products.length}
                colSpan={8}
                collapsed={collapsedGroups.has(group.label)}
                onToggle={() => toggleGroup(group.label)}
              />
              {!collapsedGroups.has(group.label) &&
                group.products.map((p) => (
                  <ProductRow
                    key={p.id}
                    product={p}
                    storeStatuses={storeStatuses}
                  />
                ))}
            </Fragment>
          ))}
        </tbody>
      </table>

      <div className="flex items-center justify-between text-sm text-slate-500">
        <span>
          {total === 0
            ? "No products"
            : `Showing ${page * PRODUCTS_PAGE_SIZE + 1}–${Math.min(page * PRODUCTS_PAGE_SIZE + (products?.length ?? 0), total)} of ${total}`}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="rounded border border-slate-300 px-3 py-1.5 disabled:opacity-40"
          >
            Prev
          </button>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(page + 1) * PRODUCTS_PAGE_SIZE >= total}
            className="rounded border border-slate-300 px-3 py-1.5 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

function ProductRow({
  product: p,
  storeStatuses,
}: {
  product: Product;
  storeStatuses: {
    platform: ListingPlatform;
    byProduct: Record<number, ProductSyncStatus>;
  }[];
}) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  const isVisible = useLazyVisible(rowRef);
  const imageUrl = useAssetUrl(isVisible ? p.main_image_asset_id : null);
  const sellable = sellableSummary(p, {
    pushBuildableCapacity: p.push_buildable_capacity,
    platformCeilingQty: p.platform_ceiling_qty,
  });

  return (
    <tr
      ref={rowRef}
      className={`border-b border-slate-100 last:border-0 hover:bg-slate-50 ${!p.is_active ? "opacity-60" : ""}`}
    >
      <td className="p-2">
        <div className="h-20 w-20 overflow-hidden rounded border border-slate-200 bg-slate-50">
          {imageUrl && (
            <img
              src={imageUrl}
              alt={p.name}
              className="h-full w-full object-cover"
            />
          )}
        </div>
      </td>
      <td className="p-2">
        <Link
          to="/products/$productId"
          params={{ productId: String(p.id) }}
          className="font-medium text-slate-900 hover:underline"
        >
          {p.name}
        </Link>
        {p.made_to_order && (
          <span
            title="Built against an order, so it is never counted"
            className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600"
          >
            made to order
          </span>
        )}
        {!p.is_active && (
          <Badge className="ml-2 bg-slate-100 text-slate-600">Inactive</Badge>
        )}
      </td>
      <td className="p-2">
        {p.sku ? (
          <span className="flex items-center gap-1">
            {p.sku}
            <CopyButton value={p.sku} label={`Copy ${p.sku}`} />
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="p-2 tabular-nums">
        {p.is_bundle
          ? `Ready to ship: ${p.ready_to_ship ?? "—"}`
          : p.current_stock}
      </td>
      {/* One column, and it's the figure that reaches the marketplaces — the three it
          replaced (max buildable / expected max buildable / max sellable) were all
          inputs to it rather than answers in their own right. */}
      <td className="p-2 tabular-nums">
        {p.is_bundle ? (
          "—"
        ) : (
          <>
            <span className="flex items-baseline gap-2">
              <strong className={sellable.headline === 0 ? "text-red-600" : ""}>
                {sellable.headline ?? "—"}
              </strong>
              {sellable.capLabel && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                  {sellable.capLabel}
                </span>
              )}
            </span>
            <span className="block text-xs text-slate-500">
              {sellable.builtFree} built + {sellable.buildable ?? 0} buildable
            </span>
          </>
        )}
      </td>
      <td className="p-2 tabular-nums">
        {p.sale_price ? formatUnitCost(p.sale_price) : "—"}
      </td>
      <td className="p-2">
        <CostLine
          label="Mat"
          base={p.cost_per_unit}
          min={p.cost_per_unit_min}
          max={p.cost_per_unit_max}
          missingLabel="no BOM"
        />
        <CostLine
          label="Kit"
          base={p.kitting_cost_per_unit}
          min={p.kitting_cost_per_unit_min}
          max={p.kitting_cost_per_unit_max}
          // No packaging BOM is a legitimate setup, not a gap — the backend is explicit
          // that null means "no packaging" rather than "packaging is free", so this stays
          // a plain dash while the other two name what's missing.
        />
        <CostLine
          label="Post"
          base={p.effective_shipping_cost}
          missingLabel="no profile"
        />
        {p.effective_shipping_profile_name && (
          <span className="block pl-9 text-xs text-slate-400">
            {p.effective_shipping_profile_name}
          </span>
        )}
      </td>
      <td className="p-2">
        <div className="flex flex-wrap gap-1">
          {storeStatuses.map(({ platform, byProduct }) => {
            const status = byProduct[p.id];
            // A product absent from a platform's index has never been checked against it
            // — that's "not listed here", so show nothing rather than a "not tested"
            // badge implying it should be.
            return status ? (
              <PlatformSyncBadge
                key={platform}
                platform={platform}
                status={status}
                compact
              />
            ) : null;
          })}
        </div>
      </td>
    </tr>
  );
}
