import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { platformsApi, type ProductSyncStatus } from "../../api/platforms";
import { productsApi } from "../../api/products";
import type { Product } from "../../api/types";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { PlatformSyncBadge } from "../../components/products/PlatformSyncBadge";
import { sellableReasonTag } from "../../lib/format";

export const Route = createFileRoute("/products/")({
  component: ProductsList,
});

function ProductsList() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["products"], queryFn: productsApi.list });
  const { data: etsyStatusByProduct } = useQuery({
    queryKey: ["platforms", "etsy", "all-sync-status"],
    queryFn: () => platformsApi.getAllSyncStatus("etsy"),
    retry: false,
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
            <th className="p-2">Max buildable</th>
            <th className="p-2">Expected max buildable</th>
            <th className="p-2">Max sellable</th>
            <th className="p-2">Cost per unit</th>
            <th className="p-2">Etsy</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((p) => (
            <ProductRow key={p.id} product={p} etsyStatus={etsyStatusByProduct?.[p.id]} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProductRow({ product: p, etsyStatus }: { product: Product; etsyStatus: ProductSyncStatus | undefined }) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  const isVisible = useLazyVisible(rowRef);
  const imageUrl = useAssetUrl(isVisible ? p.main_image_asset_id : null);

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
      <td className="p-2">{p.sku ?? "—"}</td>
      <td className="p-2">{p.is_bundle ? `Ready to ship: ${p.ready_to_ship ?? "—"}` : p.current_stock}</td>
      <td className="p-2">{p.is_bundle ? "—" : p.max_buildable ?? "No BOM set"}</td>
      <td className="p-2">{p.is_bundle ? "—" : p.expected_max_buildable ?? "No BOM set"}</td>
      <td className="p-2" title={p.is_bundle ? undefined : sellableReasonTag(p.max_sellable, p.max_buildable, p.max_sellable_reason) ?? undefined}>
        {p.is_bundle ? "—" : p.max_sellable ?? "—"}
      </td>
      <td className="p-2">{p.cost_per_unit ? `£${Number(p.cost_per_unit).toFixed(2)}` : "—"}</td>
      <td className="p-2">{etsyStatus && <PlatformSyncBadge platform="etsy" status={etsyStatus} />}</td>
    </tr>
  );
}
