import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialTypesApi } from "../../api/materialTypes";
import { coloursApi } from "../../api/colours";
import { pickFile } from "../../lib/tauri";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { useMaterialImageUrl } from "../../hooks/useMaterialImageUrl";
import { DetailPanel } from "../../components/common/DetailPanel";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { Badge } from "../../components/common/Badge";
import type { ABCClass, MaterialStockHistoryEntry, MaterialUnit } from "../../api/types";
import { StockCountFields } from "../../components/common/StockCountFields";
import { Tabs, type TabDef } from "../../components/common/Tabs";
import { FieldRow } from "../../components/common/FieldRow";
import {
  formatDayMonth,
  isLowStock,
  normalizeQtyForUnit,
  roundQty,
  wholeNumberStepFor,
} from "../../lib/format";
import { formatUnitCost } from "../../lib/money";
import {
  formatWeeksShort,
  STOCKOUT_BADGE_CLASS,
  STOCKOUT_LABEL,
  STOCKOUT_TEXT_CLASS,
} from "../../lib/forecast";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { SaveButton } from "../../components/common/SaveButton";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useSiblingNav } from "../../hooks/useSiblingNav";

const TAB_IDS = ["stock", "details", "counting", "supplier"] as const;
type TabId = (typeof TAB_IDS)[number];

const TABS: TabDef[] = [
  { id: "stock", label: "Stock" },
  { id: "details", label: "Details" },
  { id: "counting", label: "Counting" },
  { id: "supplier", label: "Supplier" },
];

const ADJUST_REASONS = [
  "Damaged / scrapped",
  "Spool ran short",
  "Found stock",
  "Correction",
  "Other…",
] as const;
const OTHER_REASON = "Other…";

export const Route = createFileRoute("/materials/$materialId")({
  component: MaterialDetail,
  // Same reasoning as the product page: keeping the tab in the URL makes switching one a
  // real router navigation, so the root unsaved-changes blocker covers leaving a dirty
  // shared edit form (Details/Counting/Supplier share one save) without this page knowing
  // the guard exists.
  validateSearch: (search: Record<string, unknown>): { tab?: TabId } => {
    // "purchasing" was this tab's id before it was renamed to "supplier" — keep old links working.
    const tab = search.tab === "purchasing" ? "supplier" : search.tab;
    return TAB_IDS.includes(tab as TabId) ? { tab: tab as TabId } : {};
  },
});

interface MaterialDetailsForm {
  name: string;
  category: string;
  unit: MaterialUnit;
  colour: string;
  materialType: string;
  materialTypeId: number | null;
  barcode: string;
  manufacturer: string;
  manufacturerId: number | null;
  productUrl: string;
  defaultSupplier: string;
  defaultSupplierId: number | null;
  typicalReorderQty: string;
  reorderThreshold: string;
  abcClass: ABCClass | null;
  stockTakeIntervalDays: string;
}

const EMPTY_MATERIAL_DETAILS: MaterialDetailsForm = {
  name: "",
  category: "",
  unit: "g",
  colour: "",
  materialType: "",
  materialTypeId: null,
  barcode: "",
  manufacturer: "",
  manufacturerId: null,
  productUrl: "",
  defaultSupplier: "",
  defaultSupplierId: null,
  typicalReorderQty: "",
  reorderThreshold: "0",
  abcClass: null,
  stockTakeIntervalDays: "",
};

interface AdjustForm {
  adjustMode: "adjust" | "set";
  adjustValue: string;
  /** A preset from ADJUST_REASONS, or "" for the unpicked state. */
  adjustReason: string;
  /** Free text, only when adjustReason is OTHER_REASON. */
  adjustReasonOther: string;
}

const EMPTY_ADJUST: AdjustForm = {
  adjustMode: "adjust",
  adjustValue: "",
  adjustReason: "",
  adjustReasonOther: "",
};

const UNITS: MaterialUnit[] = ["g", "ml", "each"];

function MaterialDetail() {
  const { materialId } = Route.useParams();
  const id = Number(materialId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const routeNavigate = Route.useNavigate();
  const activeTab: TabId = Route.useSearch().tab ?? "stock";
  const setActiveTab = (tab: string) =>
    routeNavigate({ search: { tab: tab as TabId } });
  const { prevId, nextId } = useSiblingNav(
    ["materials"],
    id,
    (data) => data as { id: number }[] | undefined,
  );
  const closePanel = useCallback(
    () => navigate({ to: "/materials" }),
    [navigate],
  );
  const goPrev = useCallback(
    () =>
      navigate({
        to: "/materials/$materialId",
        params: { materialId: String(prevId) },
      }),
    [navigate, prevId],
  );
  const goNext = useCallback(
    () =>
      navigate({
        to: "/materials/$materialId",
        params: { materialId: String(nextId) },
      }),
    [navigate, nextId],
  );

  const { data: material } = useQuery({
    queryKey: ["materials", id],
    queryFn: () => materialsApi.get(id),
  });
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const { data: history } = useQuery({
    queryKey: ["materials", id, "stock-history", historyExpanded],
    queryFn: () =>
      materialsApi.getStockHistory(id, historyExpanded ? undefined : 10),
  });
  const { data: manufacturers } = useQuery({
    queryKey: ["manufacturers"],
    queryFn: manufacturersApi.list,
  });
  const { data: suppliers } = useQuery({
    queryKey: ["suppliers"],
    queryFn: suppliersApi.list,
  });
  const { data: materialTypes } = useQuery({
    queryKey: ["material-types"],
    queryFn: materialTypesApi.list,
  });
  const { data: colourOptions } = useQuery({
    queryKey: ["colours"],
    queryFn: coloursApi.list,
  });

  const invalidateMaterial = () => {
    queryClient.invalidateQueries({ queryKey: ["materials", id] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    // current_qty/reorder_threshold changes here can flip a material's low-stock status,
    // which the dashboard caches separately — without this it can keep showing a stale
    // low-stock warning (or miss a new one) until something else happens to refetch it.
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const {
    value: imageUrlInput,
    setValue: setImageUrlInput,
    markSaved: markImageImported,
  } = useEditableCopy<string>({
    key: "material-image-url",
    label: "Material image URL",
    initial: "",
    seed: "",
    seedKey: "const",
  });
  const [isDragOver, setIsDragOver] = useState(false);

  const detailsSeed = useMemo<MaterialDetailsForm | undefined>(
    () =>
      material
        ? {
            name: material.name,
            category: material.category,
            unit: material.unit,
            colour: material.colour ?? "",
            materialType: material.material_type_name ?? "",
            materialTypeId: material.material_type_id,
            barcode: material.barcode ?? "",
            manufacturer: material.manufacturer_name ?? "",
            manufacturerId: material.manufacturer_id,
            productUrl: material.product_url ?? "",
            defaultSupplier: material.default_supplier_name ?? "",
            defaultSupplierId: material.default_supplier_id,
            typicalReorderQty: material.typical_reorder_qty ?? "",
            reorderThreshold: material.reorder_threshold,
            abcClass: material.abc_class,
            stockTakeIntervalDays:
              material.stock_take_interval_days === null
                ? ""
                : String(material.stock_take_interval_days),
          }
        : undefined,
    [material],
  );
  const {
    value: details,
    setValue: setDetails,
    isDirty: detailsDirty,
    markSaved: markDetailsSaved,
  } = useEditableCopy<MaterialDetailsForm>({
    key: "material-details",
    label: "Material details",
    initial: EMPTY_MATERIAL_DETAILS,
    seed: detailsSeed,
    seedKey: id,
  });
  const {
    name,
    category,
    unit,
    colour,
    materialType,
    materialTypeId,
    barcode,
    manufacturer,
    manufacturerId,
    productUrl,
    defaultSupplier,
    defaultSupplierId,
    typicalReorderQty,
    reorderThreshold,
    abcClass,
    stockTakeIntervalDays,
  } = details;
  const { categories, byName: categoriesByName } = useMaterialCategories();
  // Two different categories are in play: the saved one, which decides how the stats above read,
  // and the one currently selected in the form, which decides which fields the form offers.
  const editedCategory = categoriesByName.get(category);

  const setField = <K extends keyof MaterialDetailsForm>(
    field: K,
    next: MaterialDetailsForm[K],
  ) => setDetails((prev) => ({ ...prev, [field]: next }));
  const setName = (v: string) => setField("name", v);
  const setCategory = (v: string) => setField("category", v);
  const setUnit = (v: MaterialUnit) => setField("unit", v);
  const setColour = (v: string) => setField("colour", v);
  const setMaterialType = (v: string) => setField("materialType", v);
  const setMaterialTypeId = (v: number | null) => setField("materialTypeId", v);
  const setBarcode = (v: string) => setField("barcode", v);
  const setManufacturer = (v: string) => setField("manufacturer", v);
  const setManufacturerId = (v: number | null) => setField("manufacturerId", v);
  const setProductUrl = (v: string) => setField("productUrl", v);
  const setDefaultSupplier = (v: string) => setField("defaultSupplier", v);
  const setDefaultSupplierId = (v: number | null) =>
    setField("defaultSupplierId", v);
  const setTypicalReorderQty = (v: string) => setField("typicalReorderQty", v);
  const setReorderThreshold = (v: string) => setField("reorderThreshold", v);

  const saveDetailsMutation = useMutation({
    mutationFn: async () => {
      let resolvedManufacturerId = manufacturerId;
      if (!resolvedManufacturerId && manufacturer.trim()) {
        resolvedManufacturerId = (
          await manufacturersApi.findOrCreate(manufacturer.trim())
        ).id;
      }
      let resolvedSupplierId = defaultSupplierId;
      if (!resolvedSupplierId && defaultSupplier.trim()) {
        resolvedSupplierId = (
          await suppliersApi.findOrCreate(defaultSupplier.trim())
        ).id;
      }
      let resolvedMaterialTypeId = materialTypeId;
      if (!resolvedMaterialTypeId && materialType.trim()) {
        resolvedMaterialTypeId = (
          await materialTypesApi.findOrCreate(materialType.trim())
        ).id;
      }
      return materialsApi.update(id, {
        name,
        category,
        unit,
        reorder_threshold: reorderThreshold,
        colour: colour || null,
        material_type_id: resolvedMaterialTypeId,
        barcode: barcode || null,
        manufacturer_id: resolvedManufacturerId,
        default_supplier_id: resolvedSupplierId,
        typical_reorder_qty: typicalReorderQty || null,
        product_url: productUrl || null,
        abc_class: abcClass,
        stock_take_interval_days:
          stockTakeIntervalDays === "" ? null : Number(stockTakeIntervalDays),
      });
    },
    onSuccess: () => {
      markDetailsSaved();
      invalidateMaterial();
      queryClient.invalidateQueries({ queryKey: ["manufacturers"] });
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      queryClient.invalidateQueries({ queryKey: ["material-types"] });
    },
  });

  const draftPurchaseMutation = useMutation({
    mutationFn: () => materialsApi.createDraftPurchase(id),
    onSuccess: (purchase) => {
      queryClient.invalidateQueries({ queryKey: ["purchases"] });
      navigate({
        to: "/purchases/$purchaseId",
        params: { purchaseId: String(purchase.id) },
      });
    },
  });

  // Command form (records an adjustment), not an editor of stored state — it diffs against
  // its own defaults, so an abandoned half-typed adjustment still warns on navigate-away.
  const {
    value: adjust,
    setValue: setAdjust,
    markSaved: markAdjustDone,
  } = useEditableCopy<AdjustForm>({
    key: "material-adjust",
    label: "Stock adjustment",
    initial: EMPTY_ADJUST,
    seed: EMPTY_ADJUST,
    seedKey: "const",
  });
  const { adjustMode, adjustValue, adjustReason, adjustReasonOther } = adjust;
  const setAdjustMode = (v: "adjust" | "set") =>
    setAdjust((prev) => ({ ...prev, adjustMode: v }));
  const setAdjustValue = (v: string) =>
    setAdjust((prev) => ({ ...prev, adjustValue: v }));
  const setAdjustReason = (v: string) =>
    setAdjust((prev) => ({ ...prev, adjustReason: v }));
  const setAdjustReasonOther = (v: string) =>
    setAdjust((prev) => ({ ...prev, adjustReasonOther: v }));
  const effectiveReason =
    adjustReason === OTHER_REASON ? adjustReasonOther.trim() : adjustReason;
  const canAdjust = adjustValue.trim() !== "" && effectiveReason !== "";

  const adjustStockMutation = useMutation({
    mutationFn: () =>
      materialsApi.adjust(id, adjustMode, adjustValue, effectiveReason),
    onSuccess: () => {
      invalidateMaterial();
      queryClient.invalidateQueries({
        queryKey: ["materials", id, "stock-history"],
      });
      markAdjustDone(EMPTY_ADJUST);
    },
  });

  const uploadImageMutation = useMutation({
    mutationFn: () =>
      pickFile().then((picked) => {
        if (!picked) return;
        return materialsApi.uploadImage(id, picked.path, picked.name);
      }),
    onSuccess: invalidateMaterial,
  });

  const removeImageMutation = useMutation({
    mutationFn: () => materialsApi.removeImage(id),
    onSuccess: invalidateMaterial,
  });

  const importImageUrlMutation = useMutation({
    mutationFn: (url: string) => materialsApi.importImageUrl(id, url),
    onSuccess: () => {
      invalidateMaterial();
      markImageImported("");
    },
  });

  const imageUrl = useMaterialImageUrl(
    material?.image_path ? id : null,
    material?.image_path ? material.updated_at : null,
  );
  const saveDetailsStatus = useSaveStatus(saveDetailsMutation.status);

  if (!material) {
    return (
      <DetailPanel title="Loading…" onClose={closePanel}>
        <p className="text-slate-500">Loading…</p>
      </DetailPanel>
    );
  }

  const stockoutStatus = material.stockout_status;

  return (
    <DetailPanel
      title={material.name}
      onClose={closePanel}
      onPrev={prevId ? goPrev : undefined}
      onNext={nextId ? goNext : undefined}
      headerExtra={
        stockoutStatus && stockoutStatus !== "insufficient_data" ? (
          <Badge className={STOCKOUT_BADGE_CLASS[stockoutStatus]}>
            {STOCKOUT_LABEL[stockoutStatus]}
          </Badge>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-6">
        {/* Identity + the three headline figures — shown on every tab. */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <div className="h-16 w-16 shrink-0 overflow-hidden rounded border border-slate-200 bg-slate-50">
              {imageUrl && (
                <img
                  src={imageUrl}
                  alt={material.name}
                  className="h-full w-full object-cover"
                />
              )}
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-700">
                {material.material_type_name
                  ? `${material.material_type_name} · ${material.category}`
                  : material.category}
              </p>
              <p className="truncate text-[12.5px] text-slate-500">
                {material.default_supplier_name ?? "No supplier"}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <Stat
              label="On hand"
              value={`${roundQty(material.current_qty)}${material.unit === "each" ? "" : ` ${material.unit}`}`}
              sub="counted"
              tone="highlight"
            />
            <Stat
              label="On order"
              value={
                Number(material.on_order_qty ?? 0) > 0
                  ? `+${roundQty(material.on_order_qty)}`
                  : "—"
              }
              sub={
                Number(material.on_order_qty ?? 0) > 0
                  ? "on order"
                  : "nothing inbound"
              }
            />
            <Stat
              label="To stockout"
              value={formatWeeksShort(material.weeks_of_supply)}
              sub={
                (material.consumption_rate_per_week
                  ? `${roundQty(material.consumption_rate_per_week)}/wk used`
                  : "no history") +
                (material.fg_buffer_weeks && Number(material.fg_buffer_weeks) > 0.05
                  ? ` · incl. ${Number(material.fg_buffer_weeks).toFixed(1)} wk from stock`
                  : "")
              }
              valueClassName={
                stockoutStatus && stockoutStatus !== "ok"
                  ? STOCKOUT_TEXT_CLASS[stockoutStatus]
                  : undefined
              }
            />
          </div>

          <p className="text-[12px] text-slate-500">
            Used in {material.used_in_product_count ?? 0}{" "}
            {(material.used_in_product_count ?? 0) === 1 ? "product" : "products"}{" "}
            · stock value £
            {(
              Number(material.current_qty) * Number(material.avg_unit_cost)
            ).toFixed(2)}
          </p>
        </div>

        <Tabs tabs={TABS} active={activeTab} onChange={setActiveTab} />

        {activeTab === "details" && (material.barcode || material.product_url) && (
          <div className="flex gap-2">
            {material.barcode && (
              <Link
                to="/material-label/$materialId"
                params={{ materialId: String(id) }}
                className="rounded border border-slate-300 bg-white px-4 py-1.5 text-sm shadow-sm"
              >
                Print label
              </Link>
            )}
            {material.product_url && (
              <a
                href={material.product_url}
                target="_blank"
                rel="noreferrer"
                className="rounded border border-slate-300 bg-white px-4 py-1.5 text-sm shadow-sm"
              >
                Open supplier page
              </a>
            )}
          </div>
        )}

        {(activeTab === "details" ||
          activeTab === "supplier" ||
          activeTab === "counting") && (
          <section>
            <ErrorBanner error={saveDetailsMutation.error} />
            <form
              className="flex flex-col gap-3 rounded bg-white p-4 shadow-sm"
              onSubmit={(e) => {
                e.preventDefault();
                saveDetailsMutation.mutate();
              }}
            >
              {activeTab === "details" && (
                <>
                  <FieldRow label="Name">
                    <input
                      required
                      className="w-full rounded border border-slate-300 px-2 py-1"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </FieldRow>
                  <FieldRow label="Category">
                    <select
                      className="rounded border border-slate-300 px-2 py-1"
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                    >
                      {categories.map((c) => (
                        <option key={c.id} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </FieldRow>
                  <FieldRow label="Unit">
                    <select
                      className="rounded border border-slate-300 px-2 py-1"
                      value={unit}
                      onChange={(e) => setUnit(e.target.value as MaterialUnit)}
                    >
                      {UNITS.map((u) => (
                        <option key={u} value={u}>
                          {u}
                        </option>
                      ))}
                    </select>
                  </FieldRow>
                  {editedCategory?.tracks_colour && (
                    <>
                      <FieldRow label="Colour">
                        {/* Backed by the colours reference table now, so the same colour on two materials
                    is one row that can be renamed once. onResolved is unused deliberately: the
                    backend matches the name case-insensitively and find-or-creates, which is
                    more reliable than trusting an id the client resolved from a stale list. */}
                        <CreatableSelect
                          className="rounded border border-slate-300 px-2 py-1"
                          options={colourOptions ?? []}
                          value={colour}
                          onChange={setColour}
                          onResolved={() => {}}
                        />
                      </FieldRow>
                      <FieldRow label="Material type">
                        <CreatableSelect
                          className="rounded border border-slate-300 px-2 py-1"
                          options={materialTypes ?? []}
                          value={materialType}
                          onChange={setMaterialType}
                          onResolved={setMaterialTypeId}
                          placeholder="PLA, PETG…"
                        />
                      </FieldRow>
                    </>
                  )}
                  <FieldRow label="Manufacturer">
                    <CreatableSelect
                      className="rounded border border-slate-300 px-2 py-1"
                      options={manufacturers ?? []}
                      value={manufacturer}
                      onChange={setManufacturer}
                      onResolved={setManufacturerId}
                    />
                  </FieldRow>
                  <FieldRow label="Product URL">
                    <input
                      className="w-full rounded border border-slate-300 px-2 py-1"
                      value={productUrl}
                      onChange={(e) => setProductUrl(e.target.value)}
                    />
                  </FieldRow>
                  <FieldRow label="Barcode">
                    <input
                      className="rounded border border-slate-300 px-2 py-1"
                      value={barcode}
                      onChange={(e) => setBarcode(e.target.value)}
                    />
                  </FieldRow>
                  <FieldRow label="Image">
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
                            importImageUrlMutation.mutate(droppedUrl);
                          }
                          setIsDragOver(false);
                        }}
                      >
                        {imageUrl ? (
                          <img
                            src={imageUrl}
                            alt={material.name}
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <span className="text-[10px] text-slate-400">
                            no image
                          </span>
                        )}
                      </div>
                      <div className="flex flex-1 flex-col gap-2">
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => uploadImageMutation.mutate()}
                            className="rounded border border-slate-300 px-3 py-1 text-sm"
                          >
                            {material.image_path ? "Replace" : "Upload"}
                          </button>
                          {material.image_path && (
                            <button
                              type="button"
                              onClick={() => removeImageMutation.mutate()}
                              className="rounded border border-slate-300 px-3 py-1 text-sm text-red-600"
                            >
                              ×
                            </button>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <input
                            className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                            placeholder="Paste image URL, or drag a link onto the image…"
                            value={imageUrlInput}
                            onChange={(e) => setImageUrlInput(e.target.value)}
                          />
                          <button
                            type="button"
                            disabled={!imageUrlInput.trim()}
                            onClick={() =>
                              importImageUrlMutation.mutate(imageUrlInput.trim())
                            }
                            className="rounded border border-slate-300 px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            Import
                          </button>
                        </div>
                        <ErrorBanner
                          error={
                            uploadImageMutation.error ??
                            removeImageMutation.error ??
                            importImageUrlMutation.error
                          }
                        />
                      </div>
                    </div>
                  </FieldRow>
                  <FieldRow label="Used in">
                    <p className="text-sm text-slate-600">
                      {material.used_in_product_count ?? 0}{" "}
                      {(material.used_in_product_count ?? 0) === 1
                        ? "product"
                        : "products"}
                    </p>
                  </FieldRow>
                </>
              )}
              {activeTab === "supplier" && (
                <>
                  <FieldRow label="Default supplier">
                    <CreatableSelect
                      className="rounded border border-slate-300 px-2 py-1"
                      options={suppliers ?? []}
                      value={defaultSupplier}
                      onChange={setDefaultSupplier}
                      onResolved={setDefaultSupplierId}
                    />
                  </FieldRow>
                  <FieldRow label="Reorder threshold">
                    <div className="flex items-center gap-2">
                      <input
                        className="w-28 rounded border border-slate-300 px-2 py-1"
                        step="1"
                        value={reorderThreshold}
                        onChange={(e) => setReorderThreshold(e.target.value)}
                        onBlur={(e) =>
                          setReorderThreshold(
                            Math.round(Number(e.target.value) || 0).toString(),
                          )
                        }
                      />
                      <span className="text-sm text-slate-400">{unit}</span>
                    </div>
                  </FieldRow>
                  <FieldRow label="Typical reorder qty">
                    <div className="flex items-center gap-2">
                      <input
                        className="w-28 rounded border border-slate-300 px-2 py-1"
                        step="1"
                        value={typicalReorderQty}
                        onChange={(e) => setTypicalReorderQty(e.target.value)}
                        onBlur={(e) =>
                          setTypicalReorderQty(
                            e.target.value.trim() === ""
                              ? ""
                              : Math.round(Number(e.target.value) || 0).toString(),
                          )
                        }
                      />
                      <span className="text-sm text-slate-400">{unit}</span>
                    </div>
                  </FieldRow>
                  {/* Read-only — the weighted-average cost only moves via purchases and
                      adjustments, never a direct edit here. */}
                  <FieldRow
                    label={
                      editedCategory?.cost_per_kg_display
                        ? "Avg cost/kg"
                        : "Avg unit cost"
                    }
                  >
                    <div className="flex items-center gap-2">
                      <input
                        readOnly
                        className="w-28 rounded border border-slate-200 bg-slate-50 px-2 py-1 text-slate-600"
                        value={
                          editedCategory?.cost_per_kg_display
                            ? formatUnitCost(
                                Number(material.avg_unit_cost) * 1000,
                              )
                            : formatUnitCost(material.avg_unit_cost)
                        }
                      />
                      <span className="text-sm text-slate-400">
                        per {editedCategory?.cost_per_kg_display ? "kg" : unit}
                      </span>
                    </div>
                  </FieldRow>
                </>
              )}
              {activeTab === "counting" && (
                <StockCountFields
                  layout="rows"
                  abcClass={abcClass}
                  intervalDays={stockTakeIntervalDays}
                  classification={material.classification}
                  groupLabel={`the ${category} category`}
                  openTake={
                    material.open_stock_take_id
                      ? {
                          id: material.open_stock_take_id,
                          status: material.open_stock_take_line_status ?? "",
                        }
                      : null
                  }
                  onAbcClassChange={(next) => setField("abcClass", next)}
                  onIntervalDaysChange={(next) =>
                    setField("stockTakeIntervalDays", next)
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
            {activeTab === "supplier" &&
              isLowStock(material.current_qty, material.reorder_threshold) && (
                <div className="mt-2 flex items-center gap-2">
                  <button
                    onClick={() => draftPurchaseMutation.mutate()}
                    className="rounded border border-amber-300 bg-amber-50 px-3 py-1.5 text-sm text-amber-800"
                  >
                    Create draft purchase
                  </button>
                  <ErrorBanner error={draftPurchaseMutation.error} />
                </div>
              )}
            {activeTab === "supplier" && (
              <PurchaseOrderHistory history={history} unit={material.unit} />
            )}
          </section>
        )}

        {activeTab === "stock" && (
          <section>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-lg font-semibold">Stock history</h2>
              <Link
                to="/purchases/new"
                className="text-sm text-slate-600 underline"
              >
                Record a purchase
              </Link>
            </div>

            <form
              className="mb-3 flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
              onSubmit={(e) => {
                e.preventDefault();
                if (canAdjust) adjustStockMutation.mutate();
              }}
            >
              <label className="flex flex-col gap-1">
                <span className="text-sm">Mode</span>
                <select
                  className="rounded border border-slate-300 px-2 py-1"
                  value={adjustMode}
                  onChange={(e) =>
                    setAdjustMode(e.target.value as "adjust" | "set")
                  }
                >
                  <option value="adjust">Adjust (+/-)</option>
                  <option value="set">Set exact amount</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-sm">
                  {adjustMode === "set" ? "Set to" : "Adjust by"}
                </span>
                <input
                  required
                  className="w-28 rounded border border-slate-300 px-2 py-1"
                  step={wholeNumberStepFor(material.unit)}
                  placeholder={
                    adjustMode === "set" ? "e.g. 53" : "e.g. -5 or 10"
                  }
                  value={adjustValue}
                  onChange={(e) => setAdjustValue(e.target.value)}
                  onBlur={(e) =>
                    setAdjustValue(
                      normalizeQtyForUnit(e.target.value, material.unit),
                    )
                  }
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-sm">Reason</span>
                <select
                  required
                  className="rounded border border-slate-300 px-2 py-1"
                  value={adjustReason}
                  onChange={(e) => setAdjustReason(e.target.value)}
                >
                  <option value="">Pick a reason…</option>
                  {ADJUST_REASONS.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </label>
              {adjustReason === OTHER_REASON && (
                <label className="flex flex-1 flex-col gap-1">
                  <span className="text-sm">Reason detail</span>
                  <input
                    required
                    className="rounded border border-slate-300 px-2 py-1"
                    placeholder="Breakage, recount, …"
                    value={adjustReasonOther}
                    onChange={(e) => setAdjustReasonOther(e.target.value)}
                  />
                </label>
              )}
              <button
                type="submit"
                disabled={!canAdjust || adjustStockMutation.isPending}
                className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                Save
              </button>
            </form>
            {adjustValue.trim() !== "" && (
              <p className="mb-1 text-xs text-slate-500">
                On hand {roundQty(material.current_qty)} →{" "}
                {roundQty(
                  adjustMode === "set"
                    ? Number(adjustValue)
                    : Number(material.current_qty) + Number(adjustValue),
                )}
                {material.unit === "each" ? "" : ` ${material.unit}`}
              </p>
            )}
            {adjustMode === "set" && (
              <p className="text-xs text-slate-500">
                Setting an exact amount records a physical count, so this stops
                showing as due and its count date moves to today. Adjusting by
                an amount doesn't — a known change isn't a count.
              </p>
            )}
            <ErrorBanner error={adjustStockMutation.error} />

            <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
              <thead>
                <tr className="border-b border-slate-200">
                  <th className="p-2">Date</th>
                  <th className="p-2">Type</th>
                  <th className="p-2">Delta</th>
                  <th className="p-2">Comment</th>
                </tr>
              </thead>
              <tbody>
                {history?.map((h) => (
                  <tr key={`${h.kind}-${h.id}`} className="border-b border-slate-100">
                    <td className="p-2 text-slate-500">{formatDayMonth(h.at)}</td>
                    <td className="p-2">
                      <HistoryTypeBadge kind={h.kind} />
                    </td>
                    <td className="p-2">
                      <HistoryDelta h={h} unit={material.unit} />
                    </td>
                    <td className="p-2">
                      <HistoryComment h={h} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {history && (historyExpanded || history.length >= 10) && (
              <button
                type="button"
                onClick={() => setHistoryExpanded((v) => !v)}
                className="rounded border border-slate-300 px-3 py-1.5 text-sm"
              >
                {historyExpanded ? "Show fewer" : "Show full history"}
              </button>
            )}
          </section>
        )}
      </div>
    </DetailPanel>
  );
}

const HISTORY_TYPE_BADGE: Record<MaterialStockHistoryEntry["kind"], string> = {
  purchase: "bg-green-100 text-green-800",
  purchase_outstanding: "bg-amber-100 text-amber-800",
  build: "bg-blue-100 text-blue-800",
  scrap: "bg-rose-100 text-rose-800",
  adjustment: "bg-slate-100 text-slate-700",
};

const HISTORY_TYPE_LABEL: Record<MaterialStockHistoryEntry["kind"], string> = {
  purchase: "Delivered",
  purchase_outstanding: "On order",
  build: "Build",
  scrap: "Scrap",
  adjustment: "Adjustment",
};

function HistoryTypeBadge({ kind }: { kind: MaterialStockHistoryEntry["kind"] }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${HISTORY_TYPE_BADGE[kind]}`}>
      {HISTORY_TYPE_LABEL[kind]}
    </span>
  );
}

/** The qty column, renamed "Delta": unit-suffixed and coloured by direction. The `set`-mode
 * adjustment keeps its "Set to X (Δ ±Y)" form — the delta alone doesn't say what the count
 * actually landed on. */
function HistoryDelta({ h, unit }: { h: MaterialStockHistoryEntry; unit: MaterialUnit }) {
  const suffix = unit === "each" ? "" : ` ${unit}`;
  if (h.kind === "adjustment" && h.mode === "set") {
    return (
      <>
        Set to {roundQty(h.target_qty ?? "0")}
        {suffix}{" "}
        <span className="text-xs text-slate-400">
          (Δ {Number(h.qty) > 0 ? "+" : ""}
          {roundQty(h.qty)}
          {suffix})
        </span>
      </>
    );
  }
  const qty = Number(h.qty);
  const colour =
    qty > 0 ? "text-green-700" : qty < 0 ? "text-red-600" : "text-slate-500";
  return (
    <span className={colour}>
      {qty > 0 ? "+" : ""}
      {roundQty(h.qty)}
      {suffix}
    </span>
  );
}

/** Renamed from "Supplier / reason": purchase rows link to their PO, everything else keeps
 * the existing order/product link-or-plain-reason logic. */
function HistoryComment({ h }: { h: MaterialStockHistoryEntry }) {
  if (h.kind === "purchase" || h.kind === "purchase_outstanding") {
    return (
      <>
        <Link
          to="/purchases/$purchaseId"
          params={{ purchaseId: String(h.purchase_id) }}
          className="underline"
        >
          PO #{h.purchase_id}
        </Link>
        {h.supplier_name ? ` · ${h.supplier_name}` : ""}
      </>
    );
  }
  if (h.order_id != null) {
    return (
      <Link
        to="/orders/$orderId"
        params={{ orderId: String(h.order_id) }}
        className="underline"
      >
        {h.reason}
      </Link>
    );
  }
  if (h.product_id != null) {
    return (
      <Link
        to="/products/$productId"
        params={{ productId: String(h.product_id) }}
        className="underline"
      >
        {h.product_name ? `${h.reason} - ${h.product_name}` : h.reason}
      </Link>
    );
  }
  return <>{h.reason ?? "—"}</>;
}

/** The Supplier tab's PO list — reuses the Stock tab's already-fetched history query
 * (component-level, not scoped to that tab) filtered to the two purchase kinds, rather than
 * a second request. */
function PurchaseOrderHistory({
  history,
  unit,
}: {
  history: MaterialStockHistoryEntry[] | undefined;
  unit: MaterialUnit;
}) {
  const rows = (history ?? []).filter(
    (h) => h.kind === "purchase" || h.kind === "purchase_outstanding",
  );
  return (
    <div className="mt-4">
      <h3 className="mb-2 text-sm font-medium text-slate-600">
        Purchase orders
      </h3>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">No purchases yet.</p>
      ) : (
        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <tbody>
            {rows.map((h) => (
              <tr
                key={`${h.kind}-${h.id}`}
                className="border-b border-slate-100 last:border-0"
              >
                <td className="p-2 text-slate-500">{formatDayMonth(h.at)}</td>
                <td className="p-2">
                  <HistoryTypeBadge kind={h.kind} />
                </td>
                <td className="p-2">
                  <HistoryComment h={h} />
                </td>
                <td className="p-2 text-right">
                  <HistoryDelta h={h} unit={unit} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone = "default",
  valueClassName,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "default" | "highlight";
  valueClassName?: string;
}) {
  return (
    <div
      className={`rounded p-3 shadow-sm ${tone === "highlight" ? "bg-blue-50" : "bg-white"}`}
    >
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`text-lg font-semibold ${valueClassName ?? ""}`}>{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}
