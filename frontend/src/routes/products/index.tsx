import { createFileRoute, Link } from "@tanstack/react-router";
import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { platformsApi, type ProductSyncStatus } from "../../api/platforms";
import { productsApi } from "../../api/products";
import { productTypesApi } from "../../api/productTypes";
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

function ProductsList() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(0);
  const [productTypeFilter, setProductTypeFilter] = useState<number | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["products", page, productTypeFilter],
    queryFn: () => productsApi.listPaged(PRODUCTS_PAGE_SIZE, page * PRODUCTS_PAGE_SIZE, productTypeFilter),
    placeholderData: keepPreviousData,
  });
  const { data: productTypes } = useQuery({ queryKey: ["product-types"], queryFn: productTypesApi.list });
  const products = data?.items;
  const total = data?.total ?? 0;
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

      {productTypes && productTypes.length > 0 && (
        <label className="flex items-center gap-2 text-sm">
          Product type
          <select
            className="rounded border border-slate-300 px-2 py-1"
            value={productTypeFilter ?? ""}
            onChange={(e) => {
              setProductTypeFilter(e.target.value === "" ? null : Number(e.target.value));
              // Back to the first page: page 3 of the unfiltered list is usually past the
              // end of the filtered one, which would land on a blank table.
              setPage(0);
            }}
          >
            <option value="">All</option>
            {productTypes.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
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
            <th className="p-2">Type</th>
            <th className="p-2">On hand</th>
            <th className="p-2">Sellable</th>
            <th className="p-2">Cost per unit</th>
            <th className="p-2">Stores</th>
          </tr>
        </thead>
        <tbody>
          {products?.map((p) => (
            <ProductRow key={p.id} product={p} storeStatuses={storeStatuses} />
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
      <td className="p-2">{p.product_type_name ?? "—"}</td>
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
      <td className="p-2">{p.cost_per_unit ? formatUnitCost(p.cost_per_unit) : "—"}</td>
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
