import type { Material, MaterialCategory } from "../../api/types";

const CATEGORY_LABELS: Record<MaterialCategory, string> = {
  filament: "Filament",
  resin: "Resin",
  pigment: "Pigment",
  hardware: "Hardware",
  packaging: "Packaging",
  blanks: "Blanks",
  other: "Other",
};

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
  // Never hide the row's own current selection, even if it doesn't match the filter —
  // otherwise typing into the filter box can silently un-select an already-chosen material.
  const visible = materials.filter((m) => matchesFilter(m, filterText) || m.id === value);

  const byCategory = new Map<MaterialCategory, Material[]>();
  for (const m of visible) {
    const list = byCategory.get(m.category) ?? [];
    list.push(m);
    byCategory.set(m.category, list);
  }

  return (
    <select
      className={className ?? "rounded border border-slate-300 px-2 py-1"}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      {Array.from(byCategory.entries()).map(([category, group]) => (
        <optgroup key={category} label={CATEGORY_LABELS[category]}>
          {group.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.unit})
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
