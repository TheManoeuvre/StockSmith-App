import { useState } from "react";
import { MaterialSelect } from "../materials/MaterialSelect";
import type { Material } from "../../api/types";
import type { PurchaseLineInput } from "../../api/purchases";

export function PurchaseLineEditor({
  materials,
  lines,
  onChange,
}: {
  materials: Material[];
  lines: PurchaseLineInput[];
  onChange: (lines: PurchaseLineInput[]) => void;
}) {
  const [filterText, setFilterText] = useState("");

  const updateLine = (index: number, patch: Partial<PurchaseLineInput>) => {
    onChange(lines.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => onChange(lines.filter((_, i) => i !== index));

  const addLine = () => {
    const firstUnused = materials.find((m) => !lines.some((l) => l.material_id === m.id)) ?? materials[0];
    if (!firstUnused) return;
    onChange([...lines, { material_id: firstUnused.id, qty: "0", total_cost: "0" }]);
  };

  return (
    <div className="flex flex-col gap-2">
      {materials.length > 8 && (
        <input
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Filter materials…"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
      )}
      <table className="w-full border-collapse bg-white text-left text-sm shadow-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Material</th>
            <th className="p-2">Qty</th>
            <th className="p-2">Total cost (£)</th>
            <th className="p-2">Notes</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="border-b border-slate-100">
              <td className="p-2">
                <MaterialSelect
                  materials={materials}
                  value={line.material_id}
                  onChange={(material_id) => updateLine(i, { material_id })}
                  filterText={filterText}
                />
              </td>
              <td className="p-2">
                <input
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                  value={line.qty}
                  onChange={(e) => updateLine(i, { qty: e.target.value })}
                />
              </td>
              <td className="p-2">
                <input
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                  value={line.total_cost}
                  onChange={(e) => updateLine(i, { total_cost: e.target.value })}
                />
              </td>
              <td className="p-2">
                <input
                  className="rounded border border-slate-300 px-2 py-1"
                  value={line.notes ?? ""}
                  onChange={(e) => updateLine(i, { notes: e.target.value })}
                />
              </td>
              <td className="p-2">
                <button onClick={() => removeLine(i)} className="text-red-600">
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={addLine} className="w-fit rounded border border-slate-300 px-3 py-1.5 text-sm">
        + Add line
      </button>
    </div>
  );
}
