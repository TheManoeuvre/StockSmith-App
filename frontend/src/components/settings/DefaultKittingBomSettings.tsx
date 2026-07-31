import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { appSettingsApi } from "../../api/appSettings";
import { materialsApi } from "../../api/materials";
import type { KittingBomLine } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { normalizeQtyForUnit, wholeNumberStepFor } from "../../lib/format";

export function DefaultKittingBomSettings() {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["settings", "default-kitting-bom"],
    queryFn: appSettingsApi.getDefaultKittingBom,
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const [lines, setLines] = useState<KittingBomLine[]>([]);
  const [filterText, setFilterText] = useState("");

  useEffect(() => {
    if (bom) setLines(bom.map((l) => ({ material_id: l.material_id, qty_required: l.qty_required })));
  }, [bom]);

  const saveMutation = useMutation({
    mutationFn: () => appSettingsApi.replaceDefaultKittingBom(lines),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings", "default-kitting-bom"] }),
  });

  const updateLine = (index: number, patch: Partial<KittingBomLine>) => {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  };

  const removeLine = (index: number) => setLines((prev) => prev.filter((_, i) => i !== index));

  const saveStatus = useSaveStatus(saveMutation.status);

  const addLine = () => {
    const firstUnused = materials?.find((m) => !lines.some((l) => l.material_id === m.id));
    if (!firstUnused) return;
    setLines((prev) => [...prev, { material_id: firstUnused.id, qty_required: "0" }]);
  };

  return (
    <div className="flex flex-col gap-3 rounded border border-slate-300 p-3">
      <div>
        <p className="font-medium">Default kitting BOM</p>
        <p className="text-sm text-slate-500">
          Packaging materials (box, label, tape) automatically added to every new product's kitting BOM when it's
          created — pick materials you already track stock for. This is a one-time snapshot: changing it here only
          affects products created afterward, not ones that already exist.
        </p>
      </div>
      {materials && materials.length > 8 && (
        <input
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Filter materials…"
          value={filterText}
          onChange={(e) => setFilterText(e.target.value)}
        />
      )}
      <table className="w-full border-collapse bg-white text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Material</th>
            <th className="p-2">Qty required</th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="border-b border-slate-100">
              <td className="p-2">
                <MaterialSelect
                  materials={materials ?? []}
                  value={line.material_id}
                  onChange={(material_id) => updateLine(i, { material_id })}
                  filterText={filterText}
                />
              </td>
              <td className="p-2">
                <input
                  className="w-24 rounded border border-slate-300 px-2 py-1"
                  step={wholeNumberStepFor(materials?.find((m) => m.id === line.material_id)?.unit)}
                  value={line.qty_required}
                  onChange={(e) => updateLine(i, { qty_required: e.target.value })}
                  onBlur={(e) =>
                    updateLine(i, {
                      qty_required: normalizeQtyForUnit(
                        e.target.value,
                        materials?.find((m) => m.id === line.material_id)?.unit
                      ),
                    })
                  }
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
      {lines.length === 0 && <p className="text-sm text-slate-400">No default kitting materials configured.</p>}
      <div className="flex gap-2">
        <button onClick={addLine} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add material
        </button>
        <button onClick={() => saveMutation.mutate()} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
          Save
        </button>
        <SaveIndicator status={saveStatus} />
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
