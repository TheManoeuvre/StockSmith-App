import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { productsApi } from "../../api/products";
import { assetsApi } from "../../api/assets";
import { BomEditor } from "../../components/products/BomEditor";
import { KittingBomEditor } from "../../components/products/KittingBomEditor";
import { BundleItemsEditor } from "../../components/products/BundleItemsEditor";
import { VariantEditor } from "../../components/products/VariantEditor";
import { VariantAttributesEditor } from "../../components/products/VariantAttributesEditor";
import { AssetUploader } from "../../components/products/AssetUploader";
import { BuildSection } from "../../components/products/BuildSection";
import { StockAdjustmentSection } from "../../components/products/StockAdjustmentSection";
import { PricingSection } from "../../components/products/PricingSection";
import { PlatformSyncSection } from "../../components/products/PlatformSyncSection";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { SaveIndicator } from "../../components/common/SaveIndicator";
import { Tabs, type TabDef } from "../../components/common/Tabs";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { pickFile } from "../../lib/tauri";
import { sellableReasonTag } from "../../lib/format";

export const Route = createFileRoute("/products/$productId")({
  component: ProductDetail,
});

function ProductDetail() {
  const { productId } = Route.useParams();
  const id = Number(productId);
  const queryClient = useQueryClient();
  const { data: product } = useQuery({ queryKey: ["products", id], queryFn: () => productsApi.get(id) });
  const { data: variants } = useQuery({
    queryKey: ["products", id, "variants"],
    queryFn: () => productsApi.listVariants(id),
  });

  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
  const [description, setDescription] = useState("");
  const [barcode, setBarcode] = useState("");
  const [platformCeilingQty, setPlatformCeilingQty] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const [activeTab, setActiveTab] = useState("details");

  useEffect(() => {
    if (product) {
      setName(product.name);
      setSku(product.sku ?? "");
      setDescription(product.description ?? "");
      setBarcode(product.barcode ?? "");
      setPlatformCeilingQty(product.platform_ceiling_qty != null ? String(product.platform_ceiling_qty) : "");
    }
  }, [product]);

  const saveDetailsMutation = useMutation({
    mutationFn: () =>
      productsApi.update(id, {
        name,
        sku: sku || null,
        description: description || null,
        barcode: barcode || null,
        platform_ceiling_qty: platformCeilingQty.trim() ? Number(platformCeilingQty) : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const toggleBundleMutation = useMutation({
    mutationFn: (is_bundle: boolean) => productsApi.update(id, { is_bundle }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const saveDetailsStatus = useSaveStatus(saveDetailsMutation.status);

  const invalidateImage = () => {
    queryClient.invalidateQueries({ queryKey: ["products", id] });
    queryClient.invalidateQueries({ queryKey: ["products", id, "assets"] });
    queryClient.invalidateQueries({ queryKey: ["products"] });
  };

  const uploadMainImageMutation = useMutation({
    mutationFn: () =>
      pickFile().then((picked) => {
        if (!picked) return;
        return assetsApi.upload(id, picked.path, picked.name, "main_image");
      }),
    onSuccess: invalidateImage,
  });

  const importMainImageUrlMutation = useMutation({
    mutationFn: (url: string) => assetsApi.importUrl(id, url, "main_image"),
    onSuccess: invalidateImage,
  });

  const removeMainImageMutation = useMutation({
    mutationFn: (assetId: number) => assetsApi.remove(assetId),
    onSuccess: invalidateImage,
  });

  const imageUrl = useAssetUrl(product?.main_image_asset_id ?? null);

  // A product with active variants never accumulates its own current_stock/allocated_qty
  // — builds always target the variant row instead — so the top-level summary sums
  // across variants rather than showing the parent's (always-zero) columns directly. If
  // every variant has been disabled, the product is treated as if it had none (builds
  // fall back to the bare product), so the summary falls back to the product's own
  // counters too. Disabled variants' stock still counts toward the total whenever at
  // least one variant is still active — disabling one doesn't make its physical stock
  // disappear.
  const { onHand, allocated, freeStock } = useMemo(() => {
    const hasActiveVariants = (variants ?? []).some((v) => v.is_active);
    const onHand = hasActiveVariants
      ? (variants ?? []).reduce((sum, v) => sum + v.current_stock, 0)
      : product?.current_stock ?? 0;
    const allocated = hasActiveVariants
      ? (variants ?? []).reduce((sum, v) => sum + v.allocated_qty, 0)
      : product?.allocated_qty ?? 0;
    return { onHand, allocated, freeStock: onHand - allocated };
  }, [variants, product]);

  if (!product) return <p>Loading…</p>;

  const tabs: TabDef[] = [
    { id: "details", label: "Details" },
    { id: "bom", label: product.is_bundle ? "Bundle components" : "Bill of Materials" },
    { id: "kitting-bom", label: "Kitting BOM" },
    { id: "pricing", label: "Pricing" },
    ...(!product.is_bundle ? [{ id: "variants", label: "Variants" }] : []),
    { id: "platform-sync", label: "Platform Sync" },
    ...(!product.is_bundle ? [{ id: "build", label: "Build" }] : []),
    { id: "assets", label: "Assets" },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex gap-4">
        <div
          className={`flex h-48 w-48 shrink-0 items-center justify-center rounded border border-slate-200 bg-slate-50 ${isDragOver ? "ring-2 ring-slate-400" : ""}`}
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("text/uri-list")) {
              e.preventDefault();
              setIsDragOver(true);
            }
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={(e) => {
            const droppedUrl = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
            if (droppedUrl) {
              e.preventDefault();
              importMainImageUrlMutation.mutate(droppedUrl);
            }
            setIsDragOver(false);
          }}
        >
          {imageUrl ? (
            <img src={imageUrl} alt={product.name} className="h-full w-full rounded object-cover" />
          ) : (
            <span className="text-xs text-slate-400">No image</span>
          )}
        </div>
        <div className="flex-1">
          <h1 className="text-xl font-semibold">{product.name}</h1>
          <p className="text-slate-500">{product.sku ?? "No SKU"}</p>
          <div className="mt-2 flex gap-2">
            <button onClick={() => uploadMainImageMutation.mutate()} className="rounded border border-slate-300 px-3 py-1 text-sm">
              {product.main_image_asset_id ? "Replace image" : "Upload image"}
            </button>
            {product.main_image_asset_id && (
              <button
                onClick={() => removeMainImageMutation.mutate(product.main_image_asset_id!)}
                className="rounded border border-slate-300 px-3 py-1 text-sm text-red-600"
              >
                Remove image
              </button>
            )}
          </div>
          <ErrorBanner
            error={uploadMainImageMutation.error ?? importMainImageUrlMutation.error ?? removeMainImageMutation.error}
          />
          <div className="mt-2 flex flex-col gap-2 text-sm">
            {product.is_bundle ? (
              <span>Ready to ship: <strong>{product.ready_to_ship ?? "No components set"}</strong></span>
            ) : (
              <>
                <span>
                  On hand: <strong>{onHand}</strong> = Free: <strong>{freeStock}</strong> + Allocated:{" "}
                  <strong>{allocated}</strong>
                </span>
                <table className="w-fit border-collapse text-left text-xs shadow-sm">
                  <thead>
                    <tr className="border-b border-slate-200">
                      <th className="p-1.5" />
                      <th className="p-1.5">Now</th>
                      <th className="p-1.5">Expected</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b border-slate-100">
                      <td className="p-1.5 font-medium text-slate-600">Build</td>
                      <td className="p-1.5">{product.max_buildable ?? "No BOM set"}</td>
                      <td className="p-1.5">{product.expected_max_buildable ?? "No BOM set"}</td>
                    </tr>
                    <tr>
                      <td className="p-1.5 font-medium text-slate-600">Ship</td>
                      <td className="p-1.5">
                        {product.max_sellable ?? "—"}
                        {sellableReasonTag(product.max_sellable, product.max_buildable, product.max_sellable_reason) && (
                          <span className="ml-1 text-slate-400">
                            {sellableReasonTag(product.max_sellable, product.max_buildable, product.max_sellable_reason)}
                          </span>
                        )}
                      </td>
                      <td className="p-1.5">
                        {product.expected_max_sellable ?? "—"}
                        {sellableReasonTag(
                          product.expected_max_sellable,
                          product.expected_max_buildable,
                          product.expected_max_sellable_reason
                        ) && (
                          <span className="ml-1 text-slate-400">
                            {sellableReasonTag(
                              product.expected_max_sellable,
                              product.expected_max_buildable,
                              product.expected_max_sellable_reason
                            )}
                          </span>
                        )}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </>
            )}
            <span>Cost per unit: <strong>{product.cost_per_unit ? `£${Number(product.cost_per_unit).toFixed(2)}` : "—"}</strong></span>
          </div>
          <label className="mt-2 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={product.is_bundle}
              onChange={(e) => toggleBundleMutation.mutate(e.target.checked)}
            />
            This is a bundle
          </label>
          <ErrorBanner error={toggleBundleMutation.error} />
        </div>
      </div>

      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

      {activeTab === "details" && (
        <section>
          <form
            className="flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
            onSubmit={(e) => {
              e.preventDefault();
              saveDetailsMutation.mutate();
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
              <input className="rounded border border-slate-300 px-2 py-1" value={sku} onChange={(e) => setSku(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 flex-1">
              <span className="text-sm">Description</span>
              <input
                className="rounded border border-slate-300 px-2 py-1"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-sm">Barcode</span>
              <input className="rounded border border-slate-300 px-2 py-1" value={barcode} onChange={(e) => setBarcode(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-sm">Platform quantity ceiling</span>
              <input
                className="w-28 rounded border border-slate-300 px-2 py-1"
                placeholder="No cap"
                value={platformCeilingQty}
                onChange={(e) => setPlatformCeilingQty(e.target.value)}
              />
            </label>
            <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
              Save
            </button>
            <SaveIndicator status={saveDetailsStatus} />
            {product.barcode && (
              <Link
                to="/product-label/$productId"
                params={{ productId: String(id) }}
                className="rounded border border-slate-300 px-4 py-1.5 text-sm"
              >
                Print label
              </Link>
            )}
          </form>
          <p className="mt-1 text-sm text-slate-500">
            Platform quantity ceiling caps what's advertised as sellable (Max sellable / Expected max sellable, and
            what gets synced toward each variant's Etsy listing) at this value, even if stock and packaging could
            support more. Applies per variant — a variant already below the cap is unaffected. Leave blank for no cap.
          </p>
          <ErrorBanner error={saveDetailsMutation.error} />
        </section>
      )}

      {activeTab === "bom" && (
        <section>{product.is_bundle ? <BundleItemsEditor productId={id} /> : <BomEditor productId={id} />}</section>
      )}

      {activeTab === "kitting-bom" && (
        <section>
          <KittingBomEditor productId={id} />
        </section>
      )}

      {activeTab === "pricing" && (
        <section>
          <PricingSection product={product} />
        </section>
      )}

      {activeTab === "variants" && !product.is_bundle && (
        <section>
          <VariantAttributesEditor product={product} />
          <div className="mt-3">
            <VariantEditor productId={id} />
          </div>
        </section>
      )}

      {activeTab === "platform-sync" && (
        <section className="flex flex-col gap-3">
          <PlatformSyncSection productId={id} platform="etsy" />
          <PlatformSyncSection productId={id} platform="ebay" />
        </section>
      )}

      {activeTab === "build" && !product.is_bundle && (
        <section className="flex flex-col gap-6">
          <BuildSection productId={id} />
          <StockAdjustmentSection productId={id} />
        </section>
      )}

      {activeTab === "assets" && (
        <section>
          <AssetUploader productId={id} />
        </section>
      )}
    </div>
  );
}
