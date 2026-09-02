import { createFileRoute, Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState, type MouseEvent } from "react";
import { materialsApi } from "../../api/materials";
import { manufacturersApi } from "../../api/manufacturers";
import { suppliersApi } from "../../api/suppliers";
import { materialTypesApi } from "../../api/materialTypes";
import { coloursApi } from "../../api/colours";
import type { Material, MaterialUnit } from "../../api/types";
import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import { useMaterialImageUrl } from "../../hooks/useMaterialImageUrl";
import { useLazyVisible } from "../../hooks/useLazyVisible";
import { ErrorBanner } from "../../components/common/ErrorBanner";
import { useDirtyRegistration } from "../../hooks/useDirtyRegistry";
import { useGuard } from "../../hooks/useUnsavedChangesGuard";
import { CreatableSelect } from "../../components/common/CreatableSelect";
import { Badge } from "../../components/common/Badge";
import { CsvImportExport } from "../../components/common/CsvImportExport";
import { FilterTabs } from "../../components/common/FilterTabs";
import { GroupHeaderRow, Th } from "../../components/common/ListTable";
import {
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

/** At risk = the forecast flags it critical/warning (by weeks-to-stockout or the user's own
 *  reorder floor), or it's simply at/below its reorder threshold — the latter covers a new
 *  material with too little sales history to forecast. Drives the "Low stock" filter tab. */
const isAtRisk = (m: Material) =>
  m.stockout_status === "critical" ||
  m.stockout_status === "warning" ||
  isLowStock(m.current_qty, m.reorder_threshold);

export const Route = createFileRoute("/materials")({
  component: MaterialsLayout,
});

function MaterialsLayout() {
  return (
    <>
      <MaterialsListContent />
      <Outlet />
    </>
  );
}

const UNITS: MaterialUnit[] = ["g", "ml", "each"];

type SortKey =
  | "name"
  | "current_qty"
  | "on_order_qty"
  | "reorder_threshold"
  | "avg_unit_cost"
  | "weeks_of_supply";

function formatQty(qty: string | null, unit: MaterialUnit): string {
  const suffix = unit === "each" ? "#" : unit;
  return `${roundQty(qty)} ${suffix}`;
}

function MaterialsListContent() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["materials"],
    queryFn: materialsApi.list,
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
  const { categories, byName: categoriesByName } = useMaterialCategories();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  // Empty until the list loads, then the first by sort order. There is no sensible hardcoded
  // default any more — "filament" might not exist.
  const [category, setCategory] = useState<string>("");
  const [unit, setUnit] = useState<MaterialUnit>("g");
  const [unitTouchedByUser, setUnitTouchedByUser] = useState(false);
  const [reorderThreshold, setReorderThreshold] = useState("0");
  const [colour, setColour] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [materialTypeId, setMaterialTypeId] = useState<number | null>(null);
  const [barcode, setBarcode] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [manufacturerId, setManufacturerId] = useState<number | null>(null);
  const [defaultSupplier, setDefaultSupplier] = useState("");
  const [defaultSupplierId, setDefaultSupplierId] = useState<number | null>(
    null,
  );
  const [productUrl, setProductUrl] = useState("");

  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<"all" | "low" | "on-order">("all");
  const [showInactive, setShowInactive] = useState(false);
  // Collapsed category groups, by name. Session-only, like the stock-take count sheet's
  // collapse — it resets on reload, which is fine for a view toggle.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "name",
    dir: "asc",
  });

  // The selected category's row, and the two flags that decide which fields the form offers.
  // Defaults to the first category so a freshly-loaded form is never on an empty selection.
  const selectedCategory = categoriesByName.get(category) ?? categories[0];
  const tracksColour = selectedCategory?.tracks_colour ?? false;
  const tracksMaterialType = selectedCategory?.tracks_material_type ?? false;

  const createMutation = useMutation({
    mutationFn: async () => {
      let resolvedManufacturerId = manufacturerId;
      if (!resolvedManufacturerId && manufacturer.trim()) {
        resolvedManufacturerId = (
          await manufacturersApi.findOrCreate(manufacturer.trim())
        ).id;
      }
      let resolvedDefaultSupplierId = defaultSupplierId;
      if (!resolvedDefaultSupplierId && defaultSupplier.trim()) {
        resolvedDefaultSupplierId = (
          await suppliersApi.findOrCreate(defaultSupplier.trim())
        ).id;
      }
      let resolvedMaterialTypeId = materialTypeId;
      if (
        tracksMaterialType &&
        !resolvedMaterialTypeId &&
        materialType.trim()
      ) {
        resolvedMaterialTypeId = (
          await materialTypesApi.findOrCreate(materialType.trim())
        ).id;
      }
      return materialsApi.create({
        name,
        category: selectedCategory?.name ?? category,
        unit,
        reorder_threshold: reorderThreshold,
        colour: tracksColour ? colour || null : null,
        material_type_id: tracksMaterialType ? resolvedMaterialTypeId : null,
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
      setUnitTouchedByUser(false);
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

  const guard = useGuard();
  // No seeding effect and no saved baseline here — this is pending input, so "dirty" simply
  // means the user has typed something into the new-material form.
  const createFormDirty =
    showForm &&
    (name.trim() !== "" ||
      colour !== "" ||
      materialType !== "" ||
      barcode !== "" ||
      manufacturer !== "" ||
      defaultSupplier !== "" ||
      productUrl !== "" ||
      reorderThreshold !== "0");
  useDirtyRegistration("new-material", "New material", createFormDirty);

  const toggleGroup = (cat: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const toggleSort = (key: SortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );
  };

  const {
    grouped,
    inactiveCount,
    shownCount,
    tabCounts,
    trackedCount,
    atRiskCount,
    onHandValue,
  } = useMemo(() => {
    const empty = {
      grouped: [] as (readonly [string, Material[]])[],
      inactiveCount: 0,
      shownCount: 0,
      tabCounts: { all: 0, low: 0, "on-order": 0 },
      trackedCount: 0,
      atRiskCount: 0,
      onHandValue: 0,
    };
    if (!data) return empty;
    const needle = search.trim().toLowerCase();

    // Tab counts and the header stats are over the active set only, independent of the search
    // box and the selected tab — they describe the shop, not the current view.
    const active = data.filter((m) => m.is_active);
    const tabCounts = {
      all: active.length,
      low: active.filter(isAtRisk).length,
      "on-order": active.filter((m) => Number(m.on_order_qty ?? 0) > 0).length,
    };
    const onHandValue = active.reduce(
      (sum, m) => sum + Number(m.current_qty) * Number(m.avg_unit_cost),
      0,
    );

    const preFiltered = data.filter((m) => {
      if (tab === "low" && !isAtRisk(m)) return false;
      if (tab === "on-order" && !(Number(m.on_order_qty ?? 0) > 0)) return false;
      if (needle) {
        const haystack =
          `${m.name} ${m.barcode ?? ""} ${m.material_type_name ?? ""}`.toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
    const inactiveCount = preFiltered.filter((m) => !m.is_active).length;
    const filtered = showInactive
      ? preFiltered
      : preFiltered.filter((m) => m.is_active);

    const dir = sort.dir === "asc" ? 1 : -1;
    filtered.sort((a, b) => {
      if (sort.key === "name") return a.name.localeCompare(b.name) * dir;
      if (sort.key === "weeks_of_supply") {
        // A material with no forecast has no place on the urgency scale — keep it last
        // whichever way the column is sorted.
        const av = a.weeks_of_supply;
        const bv = b.weeks_of_supply;
        if (av == null && bv == null) return 0;
        if (av == null) return 1;
        if (bv == null) return -1;
        return (Number(av) - Number(bv)) * dir;
      }
      return (Number(a[sort.key] ?? 0) - Number(b[sort.key] ?? 0)) * dir;
    });

    const byCategory = new Map<string, Material[]>();
    for (const m of filtered) {
      const list = byCategory.get(m.category) ?? [];
      list.push(m);
      byCategory.set(m.category, list);
    }
    // Configured order first, then anything left over. The leftovers used to be dropped
    // silently, which was unreachable while the list was hardcoded and is not any more — a
    // category renamed in Settings while this page is open would have made its materials
    // disappear from the table entirely.
    const known = categories
      .map((c) => c.name)
      .filter((name) => byCategory.has(name));
    const unknown = Array.from(byCategory.keys()).filter(
      (name) => !categoriesByName.has(name),
    );
    const grouped = [...known, ...unknown].map(
      (name) => [name, byCategory.get(name)!] as const,
    );
    return {
      grouped,
      inactiveCount,
      shownCount: filtered.length,
      tabCounts,
      trackedCount: active.length,
      atRiskCount: tabCounts.low,
      onHandValue,
    };
  }, [
    data,
    search,
    tab,
    showInactive,
    sort,
    categories,
    categoriesByName,
  ]);

  if (isLoading) return <p>Loading materials…</p>;
  if (error) return <p className="text-red-600">{(error as Error).message}</p>;

  const sortHeader = (key: SortKey, label: string) => (
    <Th onClick={() => toggleSort(key)}>
      {label} {sort.key === key ? (sort.dir === "asc" ? "▲" : "▼") : ""}
    </Th>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Materials</h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {trackedCount} tracked · {atRiskCount} at risk · £
            {onHandValue.toFixed(2)} on hand
          </p>
        </div>
        <div className="flex items-center gap-2">
          <CsvImportExport
            onExport={materialsApi.exportCsv}
            onImport={materialsApi.importCsv}
            invalidateKey={["materials", "dashboard-summary"]}
          />
          <button
            onClick={() =>
              guard.attempt(() => setShowForm((v) => !v), {
                prefix: "new-material",
              })
            }
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
          >
            {showForm ? "Cancel" : "Add material"}
          </button>
        </div>
      </div>

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
              value={selectedCategory?.name ?? ""}
              onChange={(e) => {
                const nextCategory = e.target.value;
                setCategory(nextCategory);
                // A category with no default unit leaves whatever is selected alone — which is
                // the right answer for one the user just created and hasn't configured.
                const nextUnit =
                  categoriesByName.get(nextCategory)?.default_unit;
                if (!unitTouchedByUser && nextUnit) setUnit(nextUnit);
              }}
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
              onChange={(e) => {
                setUnit(e.target.value as MaterialUnit);
                setUnitTouchedByUser(true);
              }}
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
              step={wholeNumberStepFor(unit)}
              value={reorderThreshold}
              onChange={(e) => setReorderThreshold(e.target.value)}
              onBlur={(e) =>
                setReorderThreshold(normalizeQtyForUnit(e.target.value, unit))
              }
            />
          </label>
          {tracksColour && (
            <>
              <label className="flex flex-col gap-1">
                <span className="text-sm">Colour</span>
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
            <span className="text-sm">Barcode</span>
            <input
              className="rounded border border-slate-300 px-2 py-1"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
            />
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
          <button
            type="submit"
            disabled={!name.trim() || createMutation.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            Save
          </button>
          <ErrorBanner error={createMutation.error} />
        </form>
      )}

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <FilterTabs
          tabs={[
            { id: "all", label: "All materials", count: tabCounts.all },
            { id: "low", label: "Low stock", count: tabCounts.low },
            { id: "on-order", label: "On order", count: tabCounts["on-order"] },
          ]}
          active={tab}
          onChange={(id) => setTab(id as typeof tab)}
        />
        <div className="flex items-center gap-3">
          <input
            className="w-56 rounded border border-slate-300 px-2.5 py-1.5 text-sm"
            placeholder="Search name, barcode, type…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {inactiveCount > 0 && (
            <label className="flex shrink-0 cursor-pointer items-center gap-1 text-[12px] text-slate-500">
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)}
              />
              Show inactive ({inactiveCount})
            </label>
          )}
        </div>
      </div>

      <table className="w-full border-collapse overflow-hidden rounded-lg bg-white text-left text-[12.5px] shadow-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50/60">
            <Th>{""}</Th>
            {sortHeader("name", "Material")}
            <Th>Type</Th>
            {sortHeader("current_qty", "On hand")}
            {sortHeader("on_order_qty", "On order")}
            {sortHeader("reorder_threshold", "Reorder at")}
            {sortHeader("avg_unit_cost", "Unit cost")}
            {sortHeader("weeks_of_supply", "To stockout")}
            <Th>Supplier</Th>
          </tr>
        </thead>
        {grouped.map(([cat, materials]) => (
          <tbody key={cat}>
            <GroupHeaderRow
              label={cat}
              count={materials.length}
              colSpan={9}
              capitalize
              collapsed={collapsedGroups.has(cat)}
              onToggle={() => toggleGroup(cat)}
            />
            {!collapsedGroups.has(cat) &&
              materials.map((m) => (
                <MaterialRow
                  key={m.id}
                  material={m}
                  costPerKg={
                    categoriesByName.get(cat)?.cost_per_kg_display ?? false
                  }
                />
              ))}
          </tbody>
        ))}
      </table>

      <p className="text-[12px] text-slate-500">
        Showing {shownCount} of {trackedCount}
        {showInactive && inactiveCount > 0 ? " (incl. inactive)" : ""}
      </p>
    </div>
  );
}

// costPerKg arrives resolved rather than the row deriving it: it renders once per material,
// and the grouping loop already knows the category.
function MaterialRow({
  material: m,
  costPerKg,
}: {
  material: Material;
  costPerKg: boolean;
}) {
  const status = m.stockout_status;
  const atRisk = status === "critical" || status === "warning";
  const onOrder = Number(m.on_order_qty ?? 0) > 0;

  const navigate = useNavigate();
  // Clicking anywhere on the row opens the slide-over; clicks that land on a real control
  // inside it (the name link, later any buttons) are left to that control.
  const openDetail = (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest("a, button, input, select, label")) return;
    navigate({ to: "/materials/$materialId", params: { materialId: String(m.id) } });
  };

  const rowRef = useRef<HTMLTableRowElement>(null);
  const isVisible = useLazyVisible(rowRef);
  const imageUrl = useMaterialImageUrl(
    m.image_path && isVisible ? m.id : null,
    m.image_path && isVisible ? m.updated_at : null,
  );

  return (
    <tr
      ref={rowRef}
      onClick={openDetail}
      className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${!m.is_active ? "opacity-60" : ""}`}
    >
      <td className="py-2 pl-3 pr-1">
        {/* Fixed 80px box. An uploaded photo fills it; a colour chip sits at 75% (60px)
            centred, so a swatch reads as a swatch rather than a cropped image; a material
            with neither is left as an empty tile. */}
        <div className="flex h-20 w-20 items-center justify-center overflow-hidden rounded border border-slate-200 bg-slate-50">
          {imageUrl ? (
            <img
              src={imageUrl}
              alt={m.name}
              className="h-full w-full object-cover"
            />
          ) : m.colour_hex ? (
            <div
              className="h-[60px] w-[60px] rounded"
              style={{ backgroundColor: m.colour_hex }}
              title={m.colour ?? undefined}
            />
          ) : null}
        </div>
      </td>
      <td className="px-2 py-2">
        <Link
          to="/materials/$materialId"
          params={{ materialId: String(m.id) }}
          className="font-medium text-slate-900 hover:underline"
        >
          {m.name}
        </Link>
        {atRisk && (
          <Badge className={`ml-2 ${STOCKOUT_BADGE_CLASS[status]}`}>
            {STOCKOUT_LABEL[status]}
          </Badge>
        )}
        {!m.is_active && (
          <Badge className="ml-2 bg-slate-100 text-slate-600">Inactive</Badge>
        )}
      </td>
      <td className="px-2 py-2 text-slate-500">
        {m.material_type_name ?? "—"}
      </td>
      <td
        className={`px-2 py-2 tabular-nums ${atRisk ? `font-medium ${STOCKOUT_TEXT_CLASS[status]}` : ""}`}
      >
        {formatQty(m.current_qty, m.unit)}
      </td>
      <td className="px-2 py-2 tabular-nums">
        {onOrder ? (
          <span className="text-emerald-700">
            +{formatQty(m.on_order_qty, m.unit)}
          </span>
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="px-2 py-2 tabular-nums text-slate-500">
        {formatQty(m.reorder_threshold, m.unit)}
      </td>
      <td className="px-2 py-2 tabular-nums">
        {costPerKg
          ? `${formatUnitCost(Number(m.avg_unit_cost) * 1000)}/kg`
          : formatUnitCost(m.avg_unit_cost)}
      </td>
      <td
        className={`px-2 py-2 tabular-nums ${status && status !== "ok" ? STOCKOUT_TEXT_CLASS[status] : "text-slate-500"}`}
      >
        {formatWeeksShort(m.weeks_of_supply)}
      </td>
      <td className="px-2 py-2 text-slate-500">
        {m.default_supplier_name ?? "—"}
      </td>
    </tr>
  );
}
