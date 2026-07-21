import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { materialsApi } from "../../api/materials";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialTypesApi } from "../../api/materialTypes";
import { pickFile } from "../../lib/tauri";
import { useMaterialImageUrl } from "../../hooks/useMaterialImageUrl";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import type { MaterialCategory, MaterialUnit } from "../../api/types";
import { isLowStock, roundQty } from "../../lib/format";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { SaveIndicator } from "../../components/common/SaveIndicator";

export const Route = createFileRoute("/materials/$materialId")({
  component: MaterialDetail,
});

const CATEGORIES: MaterialCategory[] = ["filament", "resin", "pigment", "hardware", "packaging", "blanks", "other"];
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

  const invalidateMaterial = () => {
    queryClient.invalidateQueries({ queryKey: ["materials", id] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
    // current_qty/reorder_threshold changes here can flip a material's low-stock status,
    // which the dashboard caches separately — without this it can keep showing a stale
    // low-stock warning (or miss a new one) until something else happens to refetch it.
    queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
  };

  const [imageUrlInput, setImageUrlInput] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);

  const [name, setName] = useState("");
  const [category, setCategory] = useState<MaterialCategory>("filament");
  const [unit, setUnit] = useState<MaterialUnit>("g");
  const [colour, setColour] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [materialTypeId, setMaterialTypeId] = useState<number | null>(null);
  const [barcode, setBarcode] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [manufacturerId, setManufacturerId] = useState<number | null>(null);
  const [productUrl, setProductUrl] = useState("");
  const [defaultSupplier, setDefaultSupplier] = useState("");
  const [defaultSupplierId, setDefaultSupplierId] = useState<number | null>(null);
  const [typicalReorderQty, setTypicalReorderQty] = useState("");
  const [reorderThreshold, setReorderThreshold] = useState("0");

  useEffect(() => {
    if (material) {
      setName(material.name);
      setCategory(material.category);
      setUnit(material.unit);
      setColour(material.colour ?? "");
      setMaterialType(material.material_type_name ?? "");
      setMaterialTypeId(material.material_type_id);
      setBarcode(material.barcode ?? "");
      setManufacturer(material.manufacturer_name ?? "");
      setManufacturerId(material.manufacturer_id);
      setProductUrl(material.product_url ?? "");
      setDefaultSupplier(material.default_supplier_name ?? "");
      setDefaultSupplierId(material.default_supplier_id);
      setTypicalReorderQty(material.typical_reorder_qty ?? "");
      setReorderThreshold(material.reorder_threshold);
    }
  }, [material]);

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
      });
    },
    onSuccess: () => {
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

  const [adjustMode, setAdjustMode] = useState<"adjust" | "set">("adjust");
  const [adjustValue, setAdjustValue] = useState("");
  const [adjustReason, setAdjustReason] = useState("");

  const adjustStockMutation = useMutation({
    mutationFn: () => materialsApi.adjust(id, adjustMode, adjustValue, adjustReason),
    onSuccess: () => {
      invalidateMaterial();
      queryClient.invalidateQueries({ queryKey: ["materials", id, "stock-history"] });
      setAdjustValue("");
      setAdjustReason("");
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
      setImageUrlInput("");
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
            <button type="submit" className="rounded border border-slate-300 px-3 py-1 text-sm">
              Import
            </button>
          </form>
          <ErrorBanner error={uploadImageMutation.error ?? removeImageMutation.error ?? importImageUrlMutation.error} />
        </div>
        <div className="grid grid-cols-3 gap-4">
          <Stat label="On hand" value={roundQty(material.current_qty)} />
          <Stat label="On order" value={roundQty(material.on_order_qty)} />
          {material.category === "filament" ? (
            <Stat label="Avg cost/kg" value={`£${(Number(material.avg_unit_cost) * 1000).toFixed(2)}`} />
          ) : (
            <Stat label="Avg unit cost" value={`£${Number(material.avg_unit_cost).toFixed(4)}`} />
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
              onChange={(e) => setCategory(e.target.value as MaterialCategory)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
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
          {category === "filament" && (
            <>
              <label className="flex flex-col gap-1">
                <span className="text-sm">Colour / hex</span>
                <input className="rounded border border-slate-300 px-2 py-1" value={colour} onChange={(e) => setColour(e.target.value)} />
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
              value={reorderThreshold}
              onChange={(e) => setReorderThreshold(e.target.value)}
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
              value={typicalReorderQty}
              onChange={(e) => setTypicalReorderQty(e.target.value)}
            />
          </label>
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Save
          </button>
          <SaveIndicator status={saveDetailsStatus} />
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
              placeholder={adjustMode === "set" ? "e.g. 53" : "e.g. -5 or 10"}
              value={adjustValue}
              onChange={(e) => setAdjustValue(e.target.value)}
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
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Save
          </button>
        </form>
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
                      : `£${unitCost.toFixed(4)}${h.status === "ordered" ? " (quoted)" : ""}`}
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
