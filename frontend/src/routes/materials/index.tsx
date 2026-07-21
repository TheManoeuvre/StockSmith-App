import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { materialsApi } from "../../api/materials";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialTypesApi } from "../../api/materialTypes";
import type { Material, MaterialCategory, MaterialUnit } from "../../api/types";
import { useMaterialImageUrl } from "../../hooks/useMaterialImageUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { isLowStock, roundQty } from "../../lib/format";

export const Route = createFileRoute("/materials/")({
  component: MaterialsList,
});

const CATEGORIES: MaterialCategory[] = ["filament", "resin", "pigment", "hardware", "packaging", "other"];
const UNITS: MaterialUnit[] = ["g", "ml", "each"];

type SortKey = "name" | "current_qty" | "on_order_qty" | "reorder_threshold" | "avg_unit_cost";

function formatQty(qty: string | null, unit: MaterialUnit): string {
  const suffix = unit === "each" ? "#" : unit;
  return `${roundQty(qty)} ${suffix}`;
}

function MaterialsList() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: manufacturers } = useQuery({ queryKey: ["manufacturers"], queryFn: manufacturersApi.list });
  const { data: suppliers } = useQuery({ queryKey: ["suppliers"], queryFn: suppliersApi.list });
  const { data: materialTypes } = useQuery({ queryKey: ["material-types"], queryFn: materialTypesApi.list });

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [category, setCategory] = useState<MaterialCategory>("filament");
  const [unit, setUnit] = useState<MaterialUnit>("g");
  const [reorderThreshold, setReorderThreshold] = useState("0");
  const [colour, setColour] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [materialTypeId, setMaterialTypeId] = useState<number | null>(null);
  const [barcode, setBarcode] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [manufacturerId, setManufacturerId] = useState<number | null>(null);
  const [defaultSupplier, setDefaultSupplier] = useState("");
  const [defaultSupplierId, setDefaultSupplierId] = useState<number | null>(null);
  const [productUrl, setProductUrl] = useState("");

  const [search, setSearch] = useState("");
  const [lowStockOnly, setLowStockOnly] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<Set<MaterialCategory>>(new Set());
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({ key: "name", dir: "asc" });

  const createMutation = useMutation({
    mutationFn: async () => {
      let resolvedManufacturerId = manufacturerId;
      if (!resolvedManufacturerId && manufacturer.trim()) {
        resolvedManufacturerId = (await manufacturersApi.findOrCreate(manufacturer.trim())).id;
      }
      let resolvedDefaultSupplierId = defaultSupplierId;
      if (!resolvedDefaultSupplierId && defaultSupplier.trim()) {
        resolvedDefaultSupplierId = (await suppliersApi.findOrCreate(defaultSupplier.trim())).id;
      }
      let resolvedMaterialTypeId = materialTypeId;
      if (category === "filament" && !resolvedMaterialTypeId && materialType.trim()) {
        resolvedMaterialTypeId = (await materialTypesApi.findOrCreate(materialType.trim())).id;
      }
      return materialsApi.create({
        name,
        category,
        unit,
        reorder_threshold: reorderThreshold,
        colour: category === "filament" ? colour || null : null,
        material_type_id: category === "filament" ? resolvedMaterialTypeId : null,
        barcode: barcode || null,
        manufacturer_id: resolvedManufacturerId,
        default_supplier_id: resolvedDefaultSupplierId,
        product_url: productUrl || null,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["materials"] });
      queryClient.invalidateQueries({ queryKey: ["manufacturers"] });
      queryClient.invalidateQueries({ queryKey: ["suppliers"] });
      queryClient.invalidateQueries({ queryKey: ["material-types"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      setShowForm(false);
      setName("");
      setReorderThreshold("0");
      setColour("");
      setMaterialType("");
      setMaterialTypeId(null);
      setBarcode("");
      setManufacturer("");
      setManufacturerId(null);
      setDefaultSupplier("");
      setDefaultSupplierId(null);
      setProductUrl("");
    },
  });

  const toggleCategoryFilter = (c: MaterialCategory) => {
    setCategoryFilter((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  };

  const toggleSort = (key: SortKey) => {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  const { grouped, inactiveCount } = useMemo(() => {
    if (!data) return { grouped: [] as (readonly [MaterialCategory, Material[]])[], inactiveCount: 0 };
    const needle = search.trim().toLowerCase();
    const preFiltered = data.filter((m) => {
      if (categoryFilter.size > 0 && !categoryFilter.has(m.category)) return false;
      if (lowStockOnly && !isLowStock(m.current_qty, m.reorder_threshold)) return false;
      if (needle) {
        const haystack = `${m.name} ${m.barcode ?? ""} ${m.material_type_name ?? ""}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
    const inactiveCount = preFiltered.filter((m) => !m.is_active).length;
    const filtered = showInactive ? preFiltered : preFiltered.filter((m) => m.is_active);

    const dir = sort.dir === "asc" ? 1 : -1;
    filtered.sort((a, b) => {
      if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
      return (Number(a[sort.key] ?? 0) - Number(b[sort.key] ?? 0)) * dir;
    });

    const byCategory = new Map<MaterialCategory, Material[]>();
    for (const m of filtered) {
      const list = byCategory.get(m.category) ?? [];
      list.push(m);
      byCategory.set(m.category, list);
    }
    const grouped = CATEGORIES.filter((c) => byCategory.has(c)).map((c) => [c, byCategory.get(c)!] as const);
    return { grouped, inactiveCount };
  }, [data, search, lowStockOnly, showInactive, categoryFilter, sort]);

  if (isLoading) return <p>Loading materials…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  const sortHeader = (key: SortKey, label: string) => (
    <th className="p-2 cursor-pointer select-none" onClick={() => toggleSort(key)}>
      {label} {sort.key === key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
    </th>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Materials</h1>
        <button onClick={() => setShowForm((v) => !v)} className="rounded bg-slate-900 px-4 py-2 text-white">
          {showForm ? "Cancel" : "Add material"}
        </button>
      </div>

      <CsvImportExport
        onExport={materialsApi.exportCsv}
        onImport={materialsApi.importCsv}
        invalidateKey={["materials", "dashboard-summary"]}
      />

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
          <label className="flex flex-col gap-1">
            <span className="text-sm">Reorder threshold</span>
            <input
              className="w-28 rounded border border-slate-300 px-2 py-1"
              value={reorderThreshold}
              onChange={(e) => setReorderThreshold(e.target.value)}
            />
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
            <span className="text-sm">Product URL</span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              value={productUrl}
              onChange={(e) => setProductUrl(e.target.value)}
            />
          </label>
          <button type="submit" className="rounded bg-slate-900 px-4 py-1.5 text-white">
            Save
          </button>
          <ErrorBanner error={createMutation.error} />
        </form>
      )}

      <div className="flex flex-wrap items-center gap-4 rounded bg-white p-3 shadow-sm text-sm">
        <input
          className="w-56 rounded border border-slate-300 px-2 py-1"
          placeholder="Search name, barcode, type…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={lowStockOnly} onChange={(e) => setLowStockOnly(e.target.checked)} />
          Low stock only
        </label>
        {inactiveCount > 0 && (
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
            Show inactive ({inactiveCount})
          </label>
        )}
        <div className="flex flex-wrap gap-2">
          {CATEGORIES.map((c) => (
            <label key={c} className="flex items-center gap-1">
              <input type="checkbox" checked={categoryFilter.has(c)} onChange={() => toggleCategoryFilter(c)} />
              {c}
            </label>
          ))}
        </div>
      </div>

      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2"></th>
            {sortHeader("name", "Name")}
            {sortHeader("current_qty", "On hand")}
            {sortHeader("on_order_qty", "On order")}
            {sortHeader("reorder_threshold", "Reorder threshold")}
            {sortHeader("avg_unit_cost", "Avg unit cost")}
          </tr>
        </thead>
        {grouped.map(([cat, materials]) => (
          <tbody key={cat}>
            <tr>
              <td colSpan={6} className="bg-slate-100 px-2 py-1 font-medium capitalize">
                {cat}
              </td>
            </tr>
            {materials.map((m) => (
              <MaterialRow key={m.id} material={m} />
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

function MaterialRow({ material: m }: { material: Material }) {
  const rowRef = useRef<HTMLTableRowElement>(null);
  const isVisible = useLazyVisible(rowRef);
  const imageUrl = useMaterialImageUrl(
    m.image_path && isVisible ? m.id : null,
    m.image_path && isVisible ? m.updated_at : null
  );
  const low = isLowStock(m.current_qty, m.reorder_threshold);

  return (
    <tr ref={rowRef} className={`border-b border-slate-100 hover:bg-slate-50 ${!m.is_active ? "opacity-60" : ""}`}>
      <td className="p-2">
        <div className="h-16 w-16 overflow-hidden rounded border border-slate-200 bg-slate-50">
          {imageUrl && <img src={imageUrl} alt={m.name} className="h-full w-full object-cover" />}
        </div>
      </td>
      <td className="p-2">
        <Link to="/materials/$materialId" params={{ materialId: String(m.id) }} className="text-slate-900 underline">
          {m.name}
        </Link>
        {!m.is_active && (
          <span className="ml-2 rounded bg-red-100 px-2 py-0.5 text-xs text-red-700">Inactive</span>
        )}
      </td>
      <td className={`p-2 ${low ? "text-red-600" : ""}`}>{formatQty(m.current_qty, m.unit)}</td>
      <td className="p-2">{m.on_order_qty ? formatQty(m.on_order_qty, m.unit) : "—"}</td>
      <td className="p-2">{formatQty(m.reorder_threshold, m.unit)}</td>
      <td className="p-2">
        {m.category === "filament"
          ? `£${(Number(m.avg_unit_cost) * 1000).toFixed(2)}/kg`
          : `£${Number(m.avg_unit_cost).toFixed(4)}`}
      </td>
    </tr>
  );
}
