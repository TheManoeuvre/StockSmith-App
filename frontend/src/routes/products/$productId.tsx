import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { productsApi } from "../../api/products";
import { assetsApi } from "../../api/assets";
import { BomEditor } from "../../components/products/BomEditor";
import { KittingBomEditor } from "../../components/products/KittingBomEditor";
import { BundleItemsEditor } from "../../components/products/BundleItemsEditor";
import { VariantEditor } from "../../components/products/VariantEditor";
import { VariantAttributesEditor } from "../../components/products/VariantAttributesEditor";
import { AssetUploader } from "../../components/products/AssetUploader";
import { StockSection } from "../../components/products/StockSection";
import { PricingSection } from "../../components/products/PricingSection";
import { PlatformSyncSection } from "../../components/products/PlatformSyncSection";
import { formatUnitCost } from "../../lib/money";
import { CopyButton } from "../../components/common/CopyButton";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { SaveButton } from "../../components/common/SaveButton";
import { Tabs, type TabDef } from "../../components/common/Tabs";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { pickFile } from "../../lib/tauri";
import { sellableSummary } from "../../lib/format";

interface DetailsForm {
  name: string;
  sku: string;
  description: string;
  barcode: string;
  platformCeilingQty: string;
}

const EMPTY_DETAILS: DetailsForm = { name: "", sku: "", description: "", barcode: "", platformCeilingQty: "" };

const TAB_IDS = ["details", "bom", "pricing", "variants", "platform-sync", "stock", "assets"] as const;
type TabId = (typeof TAB_IDS)[number];

export const Route = createFileRoute("/products/$productId")({
  component: ProductDetail,
  // The active tab lives in the URL rather than component state, which buys two things:
  // switching tabs becomes a real router navigation (so the unsaved-changes blocker covers
  // it with the same code path as leaving the page), and a tab is linkable — the dashboard
  // can send you straight to ?tab=stock.
  // Optional, so a plain link to a product still works and doesn't have to carry
  // "?tab=details". An unrecognised value falls back to the default rather than 404ing.
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    const tab = search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

function ProductDetail() {
  // Registry, blocker and dialog all live at the root (see __root.tsx) — this page only
  // needs the guard handle for its own controls that would unmount a dirty editor.
  const guard = useGuard();
  const activeTab = Route.useSearch().tab ?? "details";
  const navigate = Route.useNavigate();
  // No guard call here: this navigates, and the blocker inside useUnsavedChangesGuard
  // intercepts it.
  const setActiveTab = (tab: string) => navigate({ search: { tab: tab as TabId } });
  const { productId } = Route.useParams();
  const id = Number(productId);
  const queryClient = useQueryClient();
  const { data: product } = useQuery({ queryKey: ["products", id], queryFn: () => productsApi.get(id) });
  const { data: variants } = useQuery({
    queryKey: ["products", id, "variants"],
    queryFn: () => productsApi.listVariants(id),
  });

  const [isDragOver, setIsDragOver] = useState(false);

  const detailsSeed = useMemo(
    () =>
      product
        ? {
            name: product.name,
            sku: product.sku ?? "",
            description: product.description ?? "",
            barcode: product.barcode ?? "",
            platformCeilingQty: product.platform_ceiling_qty != null ? String(product.platform_ceiling_qty) : "",
          }
        : undefined,
    [product]
  );
  const {
    value: details,
    setValue: setDetails,
    isDirty: detailsDirty,
    markSaved: markDetailsSaved,
  } = useEditableCopy<DetailsForm>({
    key: "details",
    label: "Details",
    initial: EMPTY_DETAILS,
    seed: detailsSeed,
    seedKey: id,
  });
  const { name, sku, description, barcode, platformCeilingQty } = details;
  const setDetailsField = <K extends keyof DetailsForm>(field: K, next: DetailsForm[K]) =>
    setDetails((prev) => ({ ...prev, [field]: next }));
  const setName = (v: string) => setDetailsField("name", v);
  const setSku = (v: string) => setDetailsField("sku", v);
  const setDescription = (v: string) => setDetailsField("description", v);
  const setBarcode = (v: string) => setDetailsField("barcode", v);
  const setPlatformCeilingQty = (v: string) => setDetailsField("platformCeilingQty", v);

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
      markDetailsSaved();
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

  const togglePushBuildableCapacityMutation = useMutation({
    mutationFn: (push_buildable_capacity: boolean) => productsApi.update(id, { push_buildable_capacity }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (is_active: boolean) => productsApi.update(id, { is_active }),
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

  const sellable = sellableSummary(product, {
    pushBuildableCapacity: product.push_buildable_capacity,
    platformCeilingQty: product.platform_ceiling_qty,
  });

  const tabs: TabDef[] = [
    { id: "details", label: "Details" },
    { id: "bom", label: "Bill of Materials" },
    { id: "pricing", label: "Pricing" },
    ...(!product.is_bundle ? [{ id: "variants", label: "Variants" }] : []),
    { id: "platform-sync", label: "Platform Sync" },
    ...(!product.is_bundle ? [{ id: "stock", label: "Stock" }] : []),
    { id: "assets", label: "Assets" },
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-stretch gap-4">
        <div className="flex w-44 shrink-0 flex-col">
          <div
            className={`flex aspect-square items-center justify-center rounded border border-slate-200 bg-slate-50 ${isDragOver ? "ring-2 ring-slate-400" : ""}`}
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
          {/* mt-auto drops the image's own actions onto the bottom edge of the details
              column beside it, while aspect-square keeps the image itself 1:1 whatever
              height that column ends up being. */}
          <div className="mt-auto flex gap-2 pt-2">
            <button
              onClick={() => uploadMainImageMutation.mutate()}
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-xs"
            >
              {product.main_image_asset_id ? "Replace image" : "Upload image"}
            </button>
            {product.main_image_asset_id && (
              <button
                onClick={() => removeMainImageMutation.mutate(product.main_image_asset_id!)}
                className="rounded border border-slate-300 px-2 py-1 text-xs text-red-600"
              >
                Remove
              </button>
            )}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1">
                <h1 className="truncate text-xl font-semibold">{product.name}</h1>
                <CopyButton value={product.name} label={`Copy ${product.name}`} />
                {!product.is_active && (
                  <span className="rounded bg-slate-200 px-2 py-0.5 text-xs text-slate-600">Inactive</span>
                )}
              </div>
              {/* Name and SKU are here to be copied into a marketplace's own listing
                  tools, so the identity line carries both plus the one figure that
                  travels with them. */}
              <div className="mt-1 flex flex-wrap items-center gap-1 text-sm text-slate-500">
                {product.sku ? (
                  <>
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-600">
                      {product.sku}
                    </span>
                    <CopyButton value={product.sku} label={`Copy ${product.sku}`} />
                  </>
                ) : (
                  <span>No SKU</span>
                )}
                <span>· {product.cost_per_unit ? `${formatUnitCost(product.cost_per_unit)} / unit` : "No unit cost"}</span>
              </div>
            </div>
            {product.is_active ? (
              <button
                onClick={() => {
                  if (window.confirm("Deactivate this product? It'll stop being sellable, but can be reactivated later.")) {
                    toggleActiveMutation.mutate(false);
                  }
                }}
                disabled={toggleActiveMutation.isPending}
                className="shrink-0 rounded border border-red-300 px-3 py-1 text-xs text-red-600 disabled:opacity-50"
              >
                Deactivate
              </button>
            ) : (
              <button
                onClick={() => toggleActiveMutation.mutate(true)}
                disabled={toggleActiveMutation.isPending}
                className="shrink-0 rounded border border-slate-300 px-3 py-1 text-xs disabled:opacity-50"
              >
                Reactivate
              </button>
            )}
          </div>
          <ErrorBanner
            error={
              uploadMainImageMutation.error ??
              importMainImageUrlMutation.error ??
              removeMainImageMutation.error ??
              toggleActiveMutation.error
            }
          />
          <div className="mt-3 border-t border-slate-200 pt-3 text-sm">
            {product.is_bundle ? (
              <span>Ready to ship: <strong>{product.ready_to_ship ?? "No components set"}</strong></span>
            ) : (
              <>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-slate-500">Sellable now</span>
                  <strong className={`text-2xl font-medium ${sellable.headline === 0 ? "text-red-600" : ""}`}>
                    {sellable.headline ?? "—"}
                  </strong>
                  {sellable.capLabel && (
                    <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">{sellable.capLabel}</span>
                  )}
                  {!product.push_buildable_capacity && (
                    <span className="text-xs text-slate-400">on-hand only</span>
                  )}
                </div>
                <div className="mt-1 leading-relaxed text-slate-500">
                  <div>
                    <strong className="font-medium text-slate-900">{freeStock}</strong> built and free{" "}
                    <span className="text-slate-400">
                      — {onHand} on hand, {allocated} reserved
                    </span>
                  </div>
                  <div>
                    {sellable.buildable == null ? (
                      "No BOM set — nothing buildable"
                    ) : (
                      <>
                        + <strong className="font-medium text-slate-900">{sellable.buildable}</strong> buildable from
                        materials on hand
                      </>
                    )}
                  </div>
                </div>
                {sellable.expected != null && (
                  <div className="mt-1 text-slate-500">
                    Once purchase orders land:{" "}
                    <strong className="font-medium text-slate-900">{sellable.expected}</strong>
                  </div>
                )}
              </>
            )}
          </div>
          {/* Stays in the header rather than moving to the Details tab with the other
              configuration: flipping it rewrites the tab set, so it has to be reachable
              — and able to warn — while an editor on another tab is dirty. */}
          <label className="mt-3 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={product.is_bundle}
              onChange={(e) => {
                const next = e.target.checked;
                guard.attempt(() => toggleBundleMutation.mutate(next));
              }}
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
            <SaveButton
              type="submit"
              isDirty={detailsDirty}
              isPending={saveDetailsMutation.isPending}
              status={saveDetailsStatus}
              className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              Save
            </SaveButton>
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
            Platform quantity ceiling caps what's advertised as sellable (the "Sellable now" figure, and what gets
            synced toward each variant's Etsy listing) at this value, even if stock and packaging could support more.
            Applies per variant — a variant already below the cap is unaffected. Leave blank for no cap.
          </p>
          {!product.is_bundle && (
            <>
              <label className="mt-2 flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={product.push_buildable_capacity}
                  onChange={(e) => togglePushBuildableCapacityMutation.mutate(e.target.checked)}
                />
                Include buildable stock when pushing to marketplaces
              </label>
              <p className="mt-1 text-sm text-slate-500">
                When on (default), marketplace pushes advertise on-hand stock plus what could be built right now
                from raw materials already in stock — not already-built, ready-to-ship stock only — on the
                reasoning that an incoming order can be backfilled by building before it ships. Still capped by
                on-hand packaging and the platform ceiling either way. Turn off for products where build lead time
                makes that backfill risky.
              </p>
              <ErrorBanner error={togglePushBuildableCapacityMutation.error} />
            </>
          )}
          <ErrorBanner error={saveDetailsMutation.error} />
        </section>
      )}

      {activeTab === "bom" && (
        <section className="flex flex-col gap-6">
          {product.is_bundle ? <BundleItemsEditor productId={id} /> : <BomEditor productId={id} />}
          {/* Bundles get this too: apply_default_kitting_bom runs for them with no is_bundle
              check and order fulfilment resolves a kitting BOM for every line, so a bundle
              really does consume packaging. Hiding the table would leave that invisible. */}
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

      {activeTab === "stock" && !product.is_bundle && (
        <section className="flex flex-col gap-6">
          <StockSection productId={id} />
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
