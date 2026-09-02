import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { productsApi } from "../../api/products";
import { assetsApi } from "../../api/assets";
import { BomEditor } from "../../components/products/BomEditor";
import { KittingBomEditor } from "../../components/products/KittingBomEditor";
import { BundleItemsEditor } from "../../components/products/BundleItemsEditor";
import { ProductStoresSettings } from "../../components/products/ProductStoresSettings";
import { SyncLog } from "../../components/products/SyncLog";
import { VariantEditor } from "../../components/products/VariantEditor";
import { VariantAttributesEditor } from "../../components/products/VariantAttributesEditor";
import { BulkBomAmendModal } from "../../components/products/BulkBomAmendModal";
import { AssetUploader } from "../../components/products/AssetUploader";
import { StockSection } from "../../components/products/StockSection";
import { PricingSection } from "../../components/products/PricingSection";
import { ProductPlatformSettingsPanel } from "../../components/products/ProductPlatformSettingsPanel";
import { PlatformSyncSection } from "../../components/products/PlatformSyncSection";
import { CopyButton } from "../../components/common/CopyButton";
import { Badge } from "../../components/common/Badge";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { FieldRow } from "../../components/common/FieldRow";
import { SaveButton } from "../../components/common/SaveButton";
import { Stat } from "../../components/common/Stat";
import { Tabs, type TabDef } from "../../components/common/Tabs";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSiblingNav } from "../../hooks/useSiblingNav";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { useAssetUrl } from "../../hooks/useAssetUrl";
import { pickFile } from "../../lib/tauri";
import { sellableSummary } from "../../lib/format";
import { productCategoriesApi } from "../../api/productCategories";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { StockCountFields } from "../../components/common/StockCountFields";
import type { ABCClass, Product } from "../../api/types";

interface DetailsForm {
  name: string;
  sku: string;
  description: string;
  barcode: string;
  productCategory: string;
  productCategoryId: number | null;
  abcClass: ABCClass | null;
  stockTakeIntervalDays: string;
}

const EMPTY_DETAILS: DetailsForm = {
  name: "",
  sku: "",
  description: "",
  barcode: "",
  productCategory: "",
  productCategoryId: null,
  abcClass: null,
  stockTakeIntervalDays: "",
};

const TAB_IDS = [
  "details",
  "bom",
  "pricing",
  "variants",
  "stores",
  "stock",
  "assets",
] as const;
type TabId = (typeof TAB_IDS)[number];

/** COGS = the three per-unit deductions an order carries; margin nets platform fees off the
 *  sale price too. Mirrors what PricingSection computes. Null where the inputs aren't there
 *  yet (no BOM → no cost; no sale price → no margin). */
function productEconomics(p: Product): {
  cogs: number | null;
  marginPct: number | null;
  missingPostage: boolean;
} {
  const mat = p.cost_per_unit != null ? Number(p.cost_per_unit) : null;
  const kit = p.kitting_cost_per_unit != null ? Number(p.kitting_cost_per_unit) : 0;
  const post =
    p.effective_shipping_cost != null ? Number(p.effective_shipping_cost) : null;
  const cogs = mat == null ? null : mat + kit + (post ?? 0);
  const salePrice = p.sale_price != null ? Number(p.sale_price) : null;
  const feePct =
    p.effective_platform_fee_percent != null
      ? Number(p.effective_platform_fee_percent)
      : 0;
  const marginPct =
    cogs != null && salePrice != null && salePrice > 0
      ? ((salePrice - cogs - (salePrice * feePct) / 100) / salePrice) * 100
      : null;
  return { cogs, marginPct, missingPostage: post == null };
}

/** The header status pill: the first blocking condition wins, else "Sellable". */
function productStatusBadge(
  p: Product,
  sellableHeadline: number | null,
): { label: string; cls: string } {
  if (p.cost_per_unit == null)
    return { label: "No BOM", cls: "bg-red-100 text-red-800" };
  if (p.effective_shipping_cost == null)
    return { label: "No shipping profile", cls: "bg-red-100 text-red-800" };
  if (sellableHeadline === 0)
    return { label: "Cannot fulfil", cls: "bg-red-100 text-red-800" };
  return { label: "Sellable", cls: "bg-emerald-100 text-emerald-800" };
}

export const Route = createFileRoute("/products/$productId")({
  component: ProductDetail,
  // The active tab lives in the URL rather than component state, which buys two things:
  // switching tabs becomes a real router navigation (so the unsaved-changes blocker covers
  // it with the same code path as leaving the page), and a tab is linkable — the dashboard
  // can send you straight to ?tab=stock.
  // Optional, so a plain link to a product still works and doesn't have to carry
  // "?tab=details". An unrecognised value falls back to the default rather than 404ing.
  //
  // variantId seeds the Stock tab's build form, so the dashboard's "Build now" can land on
  // the form with the variant the order is short of already chosen. Same leniency as `tab`:
  // anything that isn't a positive integer is dropped rather than rejected, and the Stock
  // tab checks that whatever survives is actually a variant of this product before using it.
  validateSearch: (
    search: Record<string, unknown>,
  ): { tab?: TabId; variantId?: number } => {
    // "platform-sync" was this tab's id before it was renamed "stores" — keep old links working.
    const tab = search.tab === "platform-sync" ? "stores" : search.tab;
    const variantId = Number(search.variantId);
    return {
      ...(TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {}),
      ...(Number.isInteger(variantId) && variantId > 0 ? { variantId } : {}),
    };
  },
});

function ProductDetail() {
  // Registry, blocker and dialog all live at the root (see __root.tsx) — this page only
  // needs the guard handle for its own controls that would unmount a dirty editor.
  const guard = useGuard();
  const { tab: requestedTab, variantId: preselectedVariantId } =
    Route.useSearch();
  const navigate = Route.useNavigate();
  // No guard call here: this navigates, and the blocker inside useUnsavedChangesGuard
  // intercepts it.
  const setActiveTab = (tab: string) =>
    navigate({ search: { tab: tab as TabId } });
  const { productId } = Route.useParams();
  const id = Number(productId);
  const globalNavigate = useNavigate();
  const { prevId, nextId } = useSiblingNav(
    ["products"],
    id,
    (data) => (data as { items?: { id: number }[] })?.items,
  );
  const closePanel = useCallback(
    () => globalNavigate({ to: "/products" }),
    [globalNavigate],
  );
  const goPrev = useCallback(
    () =>
      globalNavigate({
        to: "/products/$productId",
        params: { productId: String(prevId) },
      }),
    [globalNavigate, prevId],
  );
  const goNext = useCallback(
    () =>
      globalNavigate({
        to: "/products/$productId",
        params: { productId: String(nextId) },
      }),
    [globalNavigate, nextId],
  );
  const queryClient = useQueryClient();
  const { data: product } = useQuery({
    queryKey: ["products", id],
    queryFn: () => productsApi.get(id),
  });
  const { data: variants } = useQuery({
    queryKey: ["products", id, "variants"],
    queryFn: () => productsApi.listVariants(id),
  });
  const { data: productCategories } = useQuery({
    queryKey: ["product-categories"],
    queryFn: productCategoriesApi.list,
  });

  const [isDragOver, setIsDragOver] = useState(false);
  const [showBomAmend, setShowBomAmend] = useState(false);

  const detailsSeed = useMemo(
    () =>
      product
        ? {
            name: product.name,
            sku: product.sku ?? "",
            description: product.description ?? "",
            barcode: product.barcode ?? "",
            productCategory: product.product_category_name ?? "",
            productCategoryId: product.product_category_id,
            abcClass: product.abc_class,
            stockTakeIntervalDays:
              product.stock_take_interval_days === null
                ? ""
                : String(product.stock_take_interval_days),
          }
        : undefined,
    [product],
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
  const {
    name,
    sku,
    description,
    barcode,
    productCategory,
    productCategoryId,
    abcClass,
    stockTakeIntervalDays,
  } = details;
  const setDetailsField = <K extends keyof DetailsForm>(
    field: K,
    next: DetailsForm[K],
  ) => setDetails((prev) => ({ ...prev, [field]: next }));
  const setName = (v: string) => setDetailsField("name", v);
  const setSku = (v: string) => setDetailsField("sku", v);
  const setDescription = (v: string) => setDetailsField("description", v);
  const setBarcode = (v: string) => setDetailsField("barcode", v);

  const saveDetailsMutation = useMutation({
    mutationFn: async () => {
      // Same find-or-create-on-save shape the materials page uses for manufacturers and
      // types: the field accepts a typed name, and only a name with no resolved id needs
      // a row creating first.
      let resolvedProductCategoryId = productCategoryId;
      if (!resolvedProductCategoryId && productCategory.trim()) {
        resolvedProductCategoryId = (
          await productCategoriesApi.findOrCreate(productCategory.trim())
        ).id;
      }
      return productsApi.update(id, {
        name,
        sku: sku || null,
        description: description || null,
        barcode: barcode || null,
        product_category_id: productCategory.trim()
          ? resolvedProductCategoryId
          : null,
        abc_class: abcClass,
        stock_take_interval_days:
          stockTakeIntervalDays === "" ? null : Number(stockTakeIntervalDays),
      });
    },
    onSuccess: () => {
      markDetailsSaved();
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["product-categories"] });
    },
  });

  const toggleBundleMutation = useMutation({
    mutationFn: (is_bundle: boolean) => productsApi.update(id, { is_bundle }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });

  const toggleMadeToOrderMutation = useMutation({
    mutationFn: (made_to_order: boolean) =>
      productsApi.update(id, { made_to_order }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", id] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      // It changes what is due for counting, and the dashboard card reads that.
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      queryClient.invalidateQueries({ queryKey: ["stock-takes"] });
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
  const { onHand, allocated } = useMemo(() => {
    const hasActiveVariants = (variants ?? []).some((v) => v.is_active);
    const onHand = hasActiveVariants
      ? (variants ?? []).reduce((sum, v) => sum + v.current_stock, 0)
      : (product?.current_stock ?? 0);
    const allocated = hasActiveVariants
      ? (variants ?? []).reduce((sum, v) => sum + v.allocated_qty, 0)
      : (product?.allocated_qty ?? 0);
    return { onHand, allocated };
  }, [variants, product]);

  if (!product) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  // A bundle offers neither a Variants nor a Stock tab (see `tabs` below), so a link to one
  // would draw the tab bar above an empty pane. That is reachable in practice: the dashboard
  // aims "Build now" at ?tab=stock, and get_orders_awaiting_inventory doesn't exclude
  // bundles. Fall back to the default, the same way an unrecognised tab value does.
  const requested = requestedTab ?? "details";
  const activeTab =
    product.is_bundle && (requested === "stock" || requested === "variants")
      ? "details"
      : requested;

  const sellable = sellableSummary(product, {
    pushBuildableCapacity: product.push_buildable_capacity,
    platformCeilingQty: product.platform_ceiling_qty,
  });

  const tabs: TabDef[] = [
    { id: "details", label: "Details" },
    { id: "bom", label: "BOM" },
    { id: "pricing", label: "Pricing" },
    ...(!product.is_bundle ? [{ id: "variants", label: "Variants" }] : []),
    { id: "stores", label: "Stores" },
    ...(!product.is_bundle ? [{ id: "stock", label: "Stock" }] : []),
    { id: "assets", label: "Assets" },
  ];

  const econ = productEconomics(product);
  const status = productStatusBadge(product, sellable.headline);

  return (
    <DetailPanel
      title={product.name}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      headerExtra={<Badge className={status.cls}>{status.label}</Badge>}
    >
      <div className="flex flex-col gap-6">
        {/* Identity + the headline figures — shown on every tab. */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="h-16 w-16 shrink-0 overflow-hidden rounded border border-slate-200 bg-slate-50">
              {imageUrl && (
                <img
                  src={imageUrl}
                  alt={product.name}
                  className="h-full w-full object-cover"
                />
              )}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-700">
                {product.product_category_name ?? "Uncategorised"}
                {product.is_bundle ? " · bundle" : ""}
                {!product.is_active ? " · inactive" : ""}
              </p>
              <p className="flex items-center gap-1 truncate text-[12.5px] text-slate-500">
                {product.sku ? (
                  <>
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600">
                      {product.sku}
                    </span>
                    <CopyButton
                      value={product.sku}
                      label={`Copy ${product.sku}`}
                    />
                  </>
                ) : (
                  "No SKU"
                )}
              </p>
            </div>
          </div>

          {product.is_bundle ? (
            <div className="grid grid-cols-3 gap-3">
              <Stat
                label="Ready to ship"
                value={String(product.ready_to_ship ?? "—")}
                sub="assembled on demand"
                tone="highlight"
              />
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <Stat
                label="On hand"
                value={String(onHand)}
                sub={`${allocated} reserved to orders`}
              />
              <Stat
                label="Buildable"
                value={
                  sellable.buildable == null ? "—" : String(sellable.buildable)
                }
                sub={
                  sellable.buildable == null ? "no BOM set" : "from materials"
                }
                valueClassName={
                  sellable.buildable == null ? "text-amber-700" : undefined
                }
              />
              <Stat
                label="Sellable"
                value={
                  sellable.headline == null ? "—" : String(sellable.headline)
                }
                sub={
                  sellable.expected != null &&
                  sellable.expected !== sellable.headline
                    ? `${sellable.expected} once POs land`
                    : sellable.capLabel ?? "pushed to stores"
                }
                tone="highlight"
                valueClassName={
                  sellable.headline === 0 ? "text-red-600" : undefined
                }
              />
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[12px] text-slate-500">
              Margin{" "}
              {econ.marginPct == null
                ? "not costed"
                : `${econ.marginPct.toFixed(0)}%${econ.missingPostage ? " (excl. postage)" : ""}`}
              {" · "}
              COGS {econ.cogs == null ? "—" : `£${econ.cogs.toFixed(2)}`}
            </p>
            {/* Toggleable from any tab, and through the guard, because flipping it rewrites
                the tab set — a dirty editor on another tab still gets to warn. */}
            <label className="flex items-center gap-1.5 text-[12px] text-slate-500">
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
          </div>
          <ErrorBanner error={toggleBundleMutation.error} />
        </div>

        <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

        {activeTab === "details" && (
          <section>
            <div className="mb-3 flex flex-wrap gap-2">
              {product.barcode && (
                <Link
                  to="/product-label/$productId"
                  params={{ productId: String(id) }}
                  className="rounded border border-slate-300 bg-white px-4 py-1.5 text-sm shadow-sm"
                >
                  Print label
                </Link>
              )}
              {product.is_active ? (
                <button
                  onClick={() => {
                    if (
                      window.confirm(
                        "Deactivate this product? It'll stop being sellable, but can be reactivated later.",
                      )
                    ) {
                      toggleActiveMutation.mutate(false);
                    }
                  }}
                  disabled={toggleActiveMutation.isPending}
                  className="rounded border border-red-300 bg-white px-4 py-1.5 text-sm text-red-600 shadow-sm disabled:opacity-50"
                >
                  Deactivate
                </button>
              ) : (
                <button
                  onClick={() => toggleActiveMutation.mutate(true)}
                  disabled={toggleActiveMutation.isPending}
                  className="rounded border border-slate-300 bg-white px-4 py-1.5 text-sm shadow-sm disabled:opacity-50"
                >
                  Reactivate
                </button>
              )}
            </div>
            <ErrorBanner error={toggleActiveMutation.error} />

            <form
              className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm"
              onSubmit={(e) => {
                e.preventDefault();
                saveDetailsMutation.mutate();
              }}
            >
              <FieldRow label="Name">
                <input
                  required
                  className="w-full rounded border border-slate-300 px-2 py-1"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </FieldRow>
              <FieldRow label="SKU">
                <input
                  className="w-full rounded border border-slate-300 px-2 py-1"
                  value={sku}
                  onChange={(e) => setSku(e.target.value)}
                />
              </FieldRow>
              <label className="flex items-start gap-3">
                <span className="mt-1 w-36 shrink-0 text-sm text-slate-600">
                  Description
                </span>
                <textarea
                  rows={5}
                  className="min-w-0 flex-1 resize-y whitespace-pre-wrap rounded border border-slate-300 px-2 py-1"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
              <FieldRow label="Barcode">
                <input
                  className="rounded border border-slate-300 px-2 py-1"
                  value={barcode}
                  onChange={(e) => setBarcode(e.target.value)}
                />
              </FieldRow>
              <FieldRow label="Product category">
                <CreatableSelect
                  className="rounded border border-slate-300 px-2 py-1"
                  options={productCategories ?? []}
                  value={productCategory}
                  onChange={(v) => setDetailsField("productCategory", v)}
                  onResolved={(v) => setDetailsField("productCategoryId", v)}
                  placeholder="Keyring, Coaster…"
                />
              </FieldRow>
              {/* An immediate-save toggle rather than part of the buffered copy — the
                  reviewed design lists it among the Details fields. */}
              {!product.is_bundle && (
                <div className="flex items-center gap-3">
                  <span className="w-36 shrink-0 text-sm text-slate-600">
                    Made to order
                  </span>
                  <label className="flex min-w-0 flex-1 items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={product.made_to_order}
                      onChange={(e) =>
                        toggleMadeToOrderMutation.mutate(e.target.checked)
                      }
                    />
                    Built against an order — excluded from stock takes
                  </label>
                </div>
              )}
              <div className="flex items-center gap-3">
                <span className="w-36 shrink-0 text-sm text-slate-600">
                  Status
                </span>
                <span className="min-w-0 flex-1 text-sm text-slate-600">
                  {product.is_active ? "Active" : "Inactive"}
                </span>
              </div>
              <ErrorBanner error={toggleMadeToOrderMutation.error} />
              <div className="flex items-start gap-3">
                <span className="mt-1 w-36 shrink-0 text-sm text-slate-600">
                  Image
                </span>
                <div className="flex items-start gap-3">
                  <div
                    className={`flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-50 ${isDragOver ? "ring-2 ring-slate-400" : ""}`}
                    onDragOver={(e) => {
                      if (e.dataTransfer.types.includes("text/uri-list")) {
                        e.preventDefault();
                        setIsDragOver(true);
                      }
                    }}
                    onDragLeave={() => setIsDragOver(false)}
                    onDrop={(e) => {
                      const droppedUrl =
                        e.dataTransfer.getData("text/uri-list") ||
                        e.dataTransfer.getData("text/plain");
                      if (droppedUrl) {
                        e.preventDefault();
                        importMainImageUrlMutation.mutate(droppedUrl);
                      }
                      setIsDragOver(false);
                    }}
                  >
                    {imageUrl ? (
                      <img
                        src={imageUrl}
                        alt={product.name}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <span className="text-[10px] text-slate-400">
                        no image
                      </span>
                    )}
                  </div>
                  <div className="flex flex-col gap-2">
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => uploadMainImageMutation.mutate()}
                        className="rounded border border-slate-300 px-3 py-1 text-sm"
                      >
                        {product.main_image_asset_id ? "Replace" : "Upload"}
                      </button>
                      {product.main_image_asset_id && (
                        <button
                          type="button"
                          onClick={() =>
                            removeMainImageMutation.mutate(
                              product.main_image_asset_id!,
                            )
                          }
                          className="rounded border border-slate-300 px-3 py-1 text-sm text-red-600"
                        >
                          ×
                        </button>
                      )}
                    </div>
                    <p className="text-xs text-slate-400">
                      or drag an image link onto the tile
                    </p>
                    <ErrorBanner
                      error={
                        uploadMainImageMutation.error ??
                        importMainImageUrlMutation.error ??
                        removeMainImageMutation.error
                      }
                    />
                  </div>
                </div>
              </div>
              {/* Bundles hold no stock of their own, so there is nothing to count for one and
                the backend sends no classification. */}
              {!product.is_bundle && (
                <StockCountFields
                  layout="rows"
                  abcClass={abcClass}
                  intervalDays={stockTakeIntervalDays}
                  classification={product.classification}
                  groupLabel={
                    product.product_category_name
                      ? `the ${product.product_category_name} type`
                      : null
                  }
                  onAbcClassChange={(next) => setDetailsField("abcClass", next)}
                  onIntervalDaysChange={(next) =>
                    setDetailsField("stockTakeIntervalDays", next)
                  }
                />
              )}
              <div>
                <SaveButton
                  type="submit"
                  isDirty={detailsDirty}
                  isPending={saveDetailsMutation.isPending}
                  status={saveDetailsStatus}
                  className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Save
                </SaveButton>
              </div>
            </form>
            <ErrorBanner error={saveDetailsMutation.error} />
          </section>
        )}

        {activeTab === "bom" && (
          <section className="flex flex-col gap-6">
            {!product.is_bundle && product.variant_attribute1_name && (
              <div>
                <button
                  onClick={() => setShowBomAmend(true)}
                  className="rounded border border-slate-300 px-3 py-1.5 text-sm"
                >
                  Amend across variants…
                </button>
              </div>
            )}
            {showBomAmend && (
              <BulkBomAmendModal
                product={product}
                onClose={() => setShowBomAmend(false)}
              />
            )}
            {product.is_bundle ? (
              <BundleItemsEditor productId={id} />
            ) : (
              <BomEditor productId={id} />
            )}
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

        {activeTab === "stores" && (
          <section className="flex flex-col gap-6">
            <ProductStoresSettings
              product={product}
              sellable={sellable}
              onHand={onHand}
              allocated={allocated}
              showBuildableToggle={!product.is_bundle}
            />
            {(["etsy", "ebay"] as const).map((platform) => (
              <div key={platform} className="flex flex-col gap-3">
                <h3 className="text-md font-semibold capitalize">{platform}</h3>
                <ProductPlatformSettingsPanel productId={id} platform={platform} />
                <PlatformSyncSection productId={id} platform={platform} />
                <SyncLog productId={id} platform={platform} />
              </div>
            ))}
          </section>
        )}

        {activeTab === "stock" && !product.is_bundle && (
          <section className="flex flex-col gap-6">
            <StockSection
              productId={id}
              initialVariantId={preselectedVariantId}
            />
          </section>
        )}

        {activeTab === "assets" && (
          <section>
            <AssetUploader productId={id} />
          </section>
        )}
      </div>
    </DetailPanel>
  );
}
