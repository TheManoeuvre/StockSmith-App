import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { materialsApi } from "../../api/materials";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialTypesApi } from "../../api/materialTypes";
import { coloursApi } from "../../api/colours";
import { pickFile } from "../../lib/tauri";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { useMaterialImageUrl } from "../../hooks/useMaterialImageUrl";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import type { ABCClass, MaterialUnit } from "../../api/types";
import { StockCountFields } from "../../components/common/StockCountFields";
import { isLowStock, normalizeQtyForUnit, roundQty, wholeNumberStepFor } from "../../lib/format";
import { formatUnitCost } from "../../lib/money";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { SaveButton } from "../../components/common/SaveButton";
import { useEditableCopy } from "../../hooks/useEditableCopy";

export const Route = createFileRoute("/materials/$materialId")({
  component: MaterialDetail,
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
  adjustReason: string;
}

const EMPTY_ADJUST: AdjustForm = { adjustMode: "adjust", adjustValue: "", adjustReason: "" };

const UNITS: MaterialUnit[] = ["g", "ml", "each"];

function MaterialDetail() {
  const { materialId } = Route.useParams();
  const id = Number(materialId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const { data: material } = useQuery({ queryKey: ["materials", id], queryFn: () => materialsApi.get(id) });
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const { data: history } = useQuery({
    queryKey: ["materials", id, "stock-history", historyExpanded],
    queryFn: () => materialsApi.getStockHistory(id, historyExpanded ? undefined : 10),
  });
  const { data: manufacturers } = useQuery({ queryKey: ["manufacturers"], queryFn: manufacturersApi.list });
  const { data: suppliers } = useQuery({ queryKey: ["suppliers"], queryFn: suppliersApi.list });
  const { data: materialTypes } = useQuery({ queryKey: ["material-types"], queryFn: materialTypesApi.list });
  const { data: colourOptions } = useQuery({ queryKey: ["colours"], queryFn: coloursApi.list });

  const invalidateMaterial = () => {
    queryClient.invalidateQueries({ queryKey: ["materials", id] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    // current_qty/reorder_threshold changes here can flip a material's low-stock status,
    // which the dashboard caches separately — without this it can keep showing a stale
    // low-stock warning (or miss a new one) until something else happens to refetch it.
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const { value: imageUrlInput, setValue: setImageUrlInput, markSaved: markImageImported } =
    useEditableCopy<string>({
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
              material.stock_take_interval_days === null ? "" : String(material.stock_take_interval_days),
          }
        : undefined,
    [material]
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

  const setField = <K extends keyof MaterialDetailsForm>(field: K, next: MaterialDetailsForm[K]) =>
    setDetails((prev) => ({ ...prev, [field]: next }));
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
  const setDefaultSupplierId = (v: number | null) => setField("defaultSupplierId", v);
  const setTypicalReorderQty = (v: string) => setField("typicalReorderQty", v);
  const setReorderThreshold = (v: string) => setField("reorderThreshold", v);

  const saveDetailsMutation = useMutation({
    mutationFn: async () => {
      let resolvedManufacturerId = manufacturerId;
      if (!resolvedManufacturerId && manufacturer.trim()) {
        resolvedManufacturerId = (await manufacturersApi.findOrCreate(manufacturer.trim())).id;
      }
      let resolvedSupplierId = defaultSupplierId;
      if (!resolvedSupplierId && defaultSupplier.trim()) {
        resolvedSupplierId = (await suppliersApi.findOrCreate(defaultSupplier.trim())).id;
      }
      let resolvedMaterialTypeId = materialTypeId;
      if (!resolvedMaterialTypeId && materialType.trim()) {
        resolvedMaterialTypeId = (await materialTypesApi.findOrCreate(materialType.trim())).id;
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
        stock_take_interval_days: stockTakeIntervalDays === "" ? null : Number(stockTakeIntervalDays),
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
      navigate({ to: "/purchases/$purchaseId", params: { purchaseId: String(purchase.id) } });
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
  const { adjustMode, adjustValue, adjustReason } = adjust;
  const setAdjustMode = (v: "adjust" | "set") => setAdjust((prev) => ({ ...prev, adjustMode: v }));
  const setAdjustValue = (v: string) => setAdjust((prev) => ({ ...prev, adjustValue: v }));
  const setAdjustReason = (v: string) => setAdjust((prev) => ({ ...prev, adjustReason: v }));
  const canAdjust = adjustValue.trim() !== "" && adjustReason.trim() !== "";

  const adjustStockMutation = useMutation({
    mutationFn: () => materialsApi.adjust(id, adjustMode, adjustValue, adjustReason),
    onSuccess: () => {
      invalidateMaterial();
      queryClient.invalidateQueries({ queryKey: ["materials", id, "stock-history"] });
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
    material?.image_path ? material.updated_at : null
  );
  const saveDetailsStatus = useSaveStatus(saveDetailsMutation.status);

  if (!material) return <p>Loading…</p>;

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
              importImageUrlMutation.mutate(droppedUrl);
            }
            setIsDragOver(false);
          }}
        >
          {imageUrl ? (
            <img src={imageUrl} alt={material.name} className="h-full w-full rounded object-cover" />
          ) : (
            <span className="text-xs text-slate-400">No image</span>
          )}
        </div>
        <div className="flex-1">
          <h1 className="text-xl font-semibold">{material.name}</h1>
          <p className="text-slate-500">
            {material.category} · {material.unit}
          </p>
          <div className="mt-2 flex gap-2">
            <button onClick={() => uploadImageMutation.mutate()} className="rounded border border-slate-300 px-3 py-1 text-sm">
              {material.image_path ? "Replace image" : "Upload image"}
            </button>
            {material.image_path && (
              <button onClick={() => removeImageMutation.mutate()} className="rounded border border-slate-300 px-3 py-1 text-sm text-red-600">
                Remove image
              </button>
            )}
          </div>
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (imageUrlInput.trim()) importImageUrlMutation.mutate(imageUrlInput.trim());
            }}
          >
            <input
              className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="Paste image URL, or drag a link onto the image…"
              value={imageUrlInput}
              onChange={(e) => setImageUrlInput(e.target.value)}
            />
            <button
              type="submit"
              disabled={!imageUrlInput.trim()}
              className="rounded border border-slate-300 px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              Import
            </button>
          </form>
          <ErrorBanner error={uploadImageMutation.error ?? removeImageMutation.error ?? importImageUrlMutation.error} />
        </div>
        <div className="grid grid-cols-3 gap-4">
          <Stat label="On hand" value={roundQty(material.current_qty)} />
          <Stat label="On order" value={roundQty(material.on_order_qty)} />
          {categoriesByName.get(material.category)?.cost_per_kg_display ? (
            <Stat label="Avg cost/kg" value={formatUnitCost(Number(material.avg_unit_cost) * 1000)} />
          ) : (
            <Stat label="Avg unit cost" value={formatUnitCost(material.avg_unit_cost)} />
          )}
        </div>
      </div>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Details</h2>
        <ErrorBanner error={saveDetailsMutation.error} />
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
            <span className="text-sm">Category</span>
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
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Unit</span>
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
          </label>
          {editedCategory?.tracks_colour && (
            <>
              <label className="flex flex-col gap-1">
                <span className="text-sm">Colour / hex</span>
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
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-sm">Material type</span>
                <CreatableSelect
                  className="rounded border border-slate-300 px-2 py-1"
                  options={materialTypes ?? []}
                  value={materialType}
                  onChange={setMaterialType}
                  onResolved={setMaterialTypeId}
                  placeholder="PLA, PETG…"
                />
              </label>
            </>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-sm">Reorder threshold</span>
            <input
              className="w-28 rounded border border-slate-300 px-2 py-1"
              step={wholeNumberStepFor(unit)}
              value={reorderThreshold}
              onChange={(e) => setReorderThreshold(e.target.value)}
              onBlur={(e) => setReorderThreshold(normalizeQtyForUnit(e.target.value, unit))}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Barcode</span>
            <input className="rounded border border-slate-300 px-2 py-1" value={barcode} onChange={(e) => setBarcode(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Manufacturer</span>
            <CreatableSelect
              className="rounded border border-slate-300 px-2 py-1"
              options={manufacturers ?? []}
              value={manufacturer}
              onChange={setManufacturer}
              onResolved={setManufacturerId}
            />
          </label>
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-sm">Product URL</span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Default supplier</span>
            <CreatableSelect
              className="rounded border border-slate-300 px-2 py-1"
              options={suppliers ?? []}
              value={defaultSupplier}
              onChange={setDefaultSupplier}
              onResolved={setDefaultSupplierId}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">Typical reorder qty</span>
            <input
              className="w-28 rounded border border-slate-300 px-2 py-1"
              step={wholeNumberStepFor(unit)}
              value={typicalReorderQty}
              onChange={(e) => setTypicalReorderQty(e.target.value)}
              onBlur={(e) => setTypicalReorderQty(normalizeQtyForUnit(e.target.value, unit))}
            />
          </label>
          <div className="basis-full">
            <StockCountFields
              abcClass={abcClass}
              intervalDays={stockTakeIntervalDays}
              classification={material.classification}
              groupLabel={`the ${category} category`}
              onAbcClassChange={(next) => setField("abcClass", next)}
              onIntervalDaysChange={(next) => setField("stockTakeIntervalDays", next)}
            />
          </div>
          <SaveButton
            type="submit"
            isDirty={detailsDirty}
            isPending={saveDetailsMutation.isPending}
            status={saveDetailsStatus}
            className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save
          </SaveButton>
          {material.barcode && (
            <Link
              to="/material-label/$materialId"
              params={{ materialId: String(id) }}
              className="rounded border border-slate-300 px-4 py-1.5 text-sm"
            >
              Print label
            </Link>
          )}
        </form>
        {isLowStock(material.current_qty, material.reorder_threshold) && (
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
      </section>

      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Stock history</h2>
          <Link to="/purchases/new" className="text-sm text-slate-600 underline">
            Record a purchase
          </Link>
        </div>

        <form
          className="mb-3 flex flex-wrap items-end gap-2 rounded bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            if (adjustValue.trim() && adjustReason.trim()) adjustStockMutation.mutate();
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-sm">Mode</span>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              value={adjustMode}
              onChange={(e) => setAdjustMode(e.target.value as "adjust" | "set")}
            >
              <option value="adjust">Adjust (+/-)</option>
              <option value="set">Set exact amount</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm">{adjustMode === "set" ? "Set to" : "Adjust by"}</span>
            <input
              required
              className="w-28 rounded border border-slate-300 px-2 py-1"
              step={wholeNumberStepFor(material.unit)}
              placeholder={adjustMode === "set" ? "e.g. 53" : "e.g. -5 or 10"}
              value={adjustValue}
              onChange={(e) => setAdjustValue(e.target.value)}
              onBlur={(e) => setAdjustValue(normalizeQtyForUnit(e.target.value, material.unit))}
            />
          </label>
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-sm">Reason</span>
            <input
              required
              className="rounded border border-slate-300 px-2 py-1"
              placeholder="Breakage, recount, …"
              value={adjustReason}
              onChange={(e) => setAdjustReason(e.target.value)}
            />
          </label>
          <button
            type="submit"
            disabled={!canAdjust || adjustStockMutation.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save
          </button>
        </form>
        {adjustMode === "set" && (
          <p className="text-xs text-slate-500">
            Setting an exact amount records a physical count, so this stops showing as due and its
            count date moves to today. Adjusting by an amount doesn't — a known change isn't a count.
          </p>
        )}
        <ErrorBanner error={adjustStockMutation.error} />

        <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <th className="p-2">Date</th>
              <th className="p-2">Type</th>
              <th className="p-2">Qty</th>
              <th className="p-2">Total cost</th>
              <th className="p-2">Unit cost</th>
              <th className="p-2">Supplier / reason</th>
            </tr>
          </thead>
          <tbody>
            {history?.map((h) => {
              const unitCost =
                h.kind === "purchase" && h.total_cost !== null && Number(h.qty) > 0
                  ? Number(h.total_cost) / Number(h.qty)
                  : null;
              return (
                <tr key={`${h.kind}-${h.id}`} className="border-b border-slate-100">
                  <td className="p-2">{new Date(h.at).toLocaleDateString()}</td>
                  <td className="p-2">
                    {h.kind === "adjustment" ? (
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">Adjustment</span>
                    ) : (
                      <span
                        className={`rounded px-2 py-0.5 text-xs ${
                          h.status === "received" ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"
                        }`}
                      >
                        {h.status === "received" ? "Received" : "Ordered"}
                      </span>
                    )}
                  </td>
                  <td className="p-2">
                    {h.kind === "adjustment" && h.mode === "set" ? (
                      <>
                        Set to {roundQty(h.target_qty ?? "0")}{" "}
                        <span className="text-xs text-slate-400">
                          (Δ {Number(h.qty) > 0 ? "+" : ""}
                          {roundQty(h.qty)})
                        </span>
                      </>
                    ) : (
                      <>
                        {h.kind === "adjustment" && Number(h.qty) > 0 ? "+" : ""}
                        {roundQty(h.qty)}
                      </>
                    )}
                  </td>
                  <td className="p-2">{h.total_cost !== null ? `£${Number(h.total_cost).toFixed(2)}` : "—"}</td>
                  <td className="p-2">
                    {unitCost === null
                      ? "—"
                      : `${formatUnitCost(unitCost)}${h.status === "ordered" ? " (quoted)" : ""}`}
                  </td>
                  <td className="p-2">
                    {h.kind === "adjustment" ? (
                      h.order_id != null ? (
                        <Link
                          to="/orders/$orderId"
                          params={{ orderId: String(h.order_id) }}
                          className="underline"
                        >
                          {h.reason}
                        </Link>
                      ) : h.product_id != null ? (
                        <Link
                          to="/products/$productId"
                          params={{ productId: String(h.product_id) }}
                          className="underline"
                        >
                          {h.product_name ? `${h.reason} - ${h.product_name}` : h.reason}
                        </Link>
                      ) : (
                        h.reason
                      )
                    ) : (
                      h.supplier_name ?? "—"
                    )}
                  </td>
                </tr>
              );
            })}
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
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-white p-3 shadow-sm">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}
