import { createFileRoute, Link } from "@tanstack/react-router";
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useRef, useState } from "react";
import { platformsApi, type ProductSyncStatus } from "../../api/platforms";
import { productsApi } from "../../api/products";
import { productCategoriesApi } from "../../api/productCategories";
import type { ListingPlatform, Product } from "../../api/types";
import { CONNECTABLE_PLATFORMS } from "../../lib/platforms";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import { CopyButton } from "../../components/common/CopyButton";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { PlatformSyncBadge } from "../../components/products/PlatformSyncBadge";
import { sellableSummary } from "../../lib/format";
import { formatUnitCost } from "../../lib/money";

export const Route = createFileRoute("/products/")({
  component: ProductsList,
});

const PRODUCTS_PAGE_SIZE = 50;

/**
 * The page's products, split into runs of one category.
 *
 * The order is the server's — it sorts by category then name so that pagination cuts
 * through the grouping rather than scattering one category over every page — so this only
 * walks the page and notices where the heading changes. Same shape as the materials list,
 * which is the point: the two read alike now that products have a category too.
 */
function groupByCategory(products: Product[]): { key: string; label: string; products: Product[] }[] {
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
  const value =
    isRange
      ? `${formatUnitCost(min)} – ${formatUnitCost(max)}`
      : min != null
        ? formatUnitCost(min)
        : base != null
          ? formatUnitCost(base)
          : null;
  return (
    <span className="block whitespace-nowrap">
      <span className="inline-block w-8 text-xs text-slate-400">{label}</span>
      {value ?? <span className={missingLabel ? "text-amber-700" : undefined}>— {missingLabel ?? ""}</span>}
    </span>
  );
}

function ProductsList() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(0);
  const [productCategoryFilter, setProductCategoryFilter] = useState<number | null>(null);
  const [cogsIncompleteOnly, setCogsIncompleteOnly] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["products", page, productCategoryFilter, cogsIncompleteOnly],
    queryFn: () =>
      productsApi.listPaged(PRODUCTS_PAGE_SIZE, page * PRODUCTS_PAGE_SIZE, productCategoryFilter, cogsIncompleteOnly),
    placeholderData: keepPreviousData,
  });
  const { data: productCategories } = useQuery({ queryKey: ["product-categories"], queryFn: productCategoriesApi.list });
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
  const storeStatuses: { platform: ListingPlatform; byProduct: Record<number, ProductSyncStatus> }[] =
    CONNECTABLE_PLATFORMS.flatMap((platform, i) => {
      const result = storeStatusQueries[i];
      return result?.data ? [{ platform, byProduct: result.data }] : [];
    });
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");

  const createMutation = useMutation({
    mutationFn: () => productsApi.create({ name, sku: sku || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
      setShowForm(false);
      setName("");
      setSku("");
    },
  });

  const groups = useMemo(() => groupByCategory(products ?? []), [products]);

  if (isLoading) return <p>Loading products…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Products</h1>
        <button onClick={() => setShowForm((v) => !v)} className="rounded bg-slate-900 px-4 py-2 text-white">
          {showForm ? "Cancel" : "Add product"}
        </button>
      </div>

      <CsvImportExport onExport={productsApi.exportCsv} onImport={productsApi.importCsv} invalidateKey="products" />

      {productCategories && productCategories.length > 0 && (
        <label className="flex items-center gap-2 text-sm">
          Product category
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={productCategoryFilter ?? ""}
            onChange={(e) => {
              setProductCategoryFilter(e.target.value === "" ? null : Number(e.target.value));
              // Back to the first page: page 3 of the unfiltered list is usually past the
              // end of the filtered one, which would land on a blank table.
              setPage(0);
            }}
          >
            <option value="">All</option>
            {productCategories.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* The count is what makes this an active signal rather than a dash to go hunting
          for: it is reported whether or not the filter is on, so a gap announces itself
          without the user first suspecting there is one. Hidden entirely at zero — there is
          nothing to act on, and a permanent "0" trains people to stop reading it. */}
      {incompleteTotal > 0 && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={cogsIncompleteOnly}
            onChange={(e) => {
              setCogsIncompleteOnly(e.target.checked);
              setPage(0);
            }}
          />
          Incomplete COGS only
          <span
            className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
            title="Products with no shipping profile, or no materials cost — their orders can't report a truthful profit."
          >
            {incompleteTotal}
          </span>
        </label>
      )}

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
            <input required className="rounded border border-slate-300 px-2 py-1" value={name} onChange={(e) => setName(e.target.value)} />
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
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Save
          </button>
          <ErrorBanner error={createMutation.error} />
        </form>
      )}

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2"></th>
            <th className="p-2">Name</th>
            <th className="p-2">SKU</th>

            <th className="p-2">On hand</th>
            <th className="p-2">Sellable</th>
            <th className="p-2">Sale price</th>
            {/* The three deductions that make up an order's cost of goods, stacked in one
                column rather than three: at eleven columns wide the table stopped being
                scannable, and these are read together or not at all. */}
            <th className="p-2">Costs</th>
            <th className="p-2">Stores</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <Fragment key={group.key}>
              <tr className="border-b border-slate-200 bg-slate-50">
                <th colSpan={8} className="p-2 text-left font-medium">
                  {group.label}
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    {group.products.length} {group.products.length === 1 ? "product" : "products"}
                  </span>
                </th>
              </tr>
              {group.products.map((p) => (
                <ProductRow key={p.id} product={p} storeStatuses={storeStatuses} />
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
  storeStatuses: { platform: ListingPlatform; byProduct: Record<number, ProductSyncStatus> }[];
}) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  const isVisible = useLazyVisible(rowRef);
  const imageUrl = useAssetUrl(isVisible ? p.main_image_asset_id : null);
  const sellable = sellableSummary(p, {
    pushBuildableCapacity: p.push_buildable_capacity,
    platformCeilingQty: p.platform_ceiling_qty,
  });

  return (
    <tr ref={rowRef} className="border-b border-slate-100 hover:bg-slate-50">
      <td className="p-2">
        <div className="h-16 w-16 overflow-hidden rounded border border-slate-200 bg-slate-50">
          {imageUrl && <img src={imageUrl} alt={p.name} className="h-full w-full object-cover" />}
        </div>
      </td>
      <td className="p-2">
        <Link to="/products/$productId" params={{ productId: String(p.id) }} className="text-slate-900 underline">
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
      <td className="p-2">{p.is_bundle ? `Ready to ship: ${p.ready_to_ship ?? "—"}` : p.current_stock}</td>
      {/* One column, and it's the figure that reaches the marketplaces — the three it
          replaced (max buildable / expected max buildable / max sellable) were all
          inputs to it rather than answers in their own right. */}
      <td className="p-2">
        {p.is_bundle ? (
          "—"
        ) : (
          <>
            <span className="flex items-baseline gap-2">
              <strong className={sellable.headline === 0 ? "text-red-600" : ""}>{sellable.headline ?? "—"}</strong>
              {sellable.capLabel && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">{sellable.capLabel}</span>
              )}
            </span>
            <span className="block text-xs text-slate-500">
              {sellable.builtFree} built + {sellable.buildable ?? 0} buildable
            </span>
          </>
        )}
      </td>
      <td className="p-2">{p.sale_price ? formatUnitCost(p.sale_price) : "—"}</td>
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
        <CostLine label="Post" base={p.effective_shipping_cost} missingLabel="no profile" />
        {p.effective_shipping_profile_name && (
          <span className="block pl-9 text-xs text-slate-400">{p.effective_shipping_profile_name}</span>
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
              <PlatformSyncBadge key={platform} platform={platform} status={status} compact />
            ) : null;
          })}
        </div>
      </td>
    </tr>
  );
}
