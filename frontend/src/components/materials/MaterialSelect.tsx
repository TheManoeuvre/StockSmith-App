import { useMaterialCategories } from "../../hooks/useMaterialCategories";
import type { Material } from "../../api/types";

function matchesFilter(material: Material, filterText: string): boolean {
  if (!filterText.trim()) return true;
  const needle = filterText.trim().toLowerCase();
  return (
    material.name.toLowerCase().includes(needle) ||
    (material.barcode ?? "").toLowerCase().includes(needle) ||
    (material.material_type_name ?? "").toLowerCase().includes(needle)
  );
}

export function MaterialSelect({
  materials,
  value,
  onChange,
  filterText = "",
  className,
}: {
  materials: Material[];
  value: number;
  onChange: (materialId: number) => void;
  filterText?: string;
  className?: string;
}) {
  const { categories } = useMaterialCategories();

  // Never hide the row's own current selection, even if it doesn't match the filter —
  // otherwise typing into the filter box can silently un-select an already-chosen material.
  const visible = materials.filter((m) => matchesFilter(m, filterText) || m.id === value);

  const byCategory = new Map<string, Material[]>();
  for (const m of visible) {
    const list = byCategory.get(m.category) ?? [];
    list.push(m);
    byCategory.set(m.category, list);
  }

  // Configured order, not the order materials happened to arrive in — which is what this used
  // to do, so the BOM picker grouped categories differently from the materials table for no
  // reason anyone chose. Any category not in the list still renders, at the end: it can only
  // mean the list is mid-refetch, and dropping the group would drop the material.
  const known = categories.map((c) => c.name).filter((name) => byCategory.has(name));
  const unknown = Array.from(byCategory.keys()).filter((name) => !categories.some((c) => c.name === name));
  const ordered = [...known, ...unknown];

  return (
    <select
      className={className ?? "rounded border border-slate-300 px-2 py-1"}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      {ordered.map((category) => (
        <optgroup key={category} label={category} className="capitalize">
          {(byCategory.get(category) ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.unit})
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
