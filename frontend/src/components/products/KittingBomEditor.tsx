import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { materialsApi } from "../../api/materials";
import { productsApi } from "../../api/products";
import type { KittingBomLine } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";

export function KittingBomEditor({ productId }: { productId: number }) {
  const queryClient = useQueryClient();
  const { data: bom } = useQuery({
    queryKey: ["products", productId, "kitting-bom"],
    queryFn: () => productsApi.getKittingBom(productId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const [lines, setLines] = useState<KittingBomLine[]>([]);
  const [filterText, setFilterText] = useState("");

  useEffect(() => {
    if (bom) setLines(bom.map((l) => ({ material_id: l.material_id, qty_required: l.qty_required })));
  }, [bom]);

  const saveMutation = useMutation({
    mutationFn: () => productsApi.replaceKittingBom(productId, lines),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products", productId] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "kitting-bom"] });
      queryClient.invalidateQueries({ queryKey: ["products", productId, "variants"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
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

  const maxTheoretical = (line: KittingBomLine): number | null => {
    const material = materials?.find((m) => m.id === line.material_id);
    const qtyRequired = Number(line.qty_required);
    if (!material || !qtyRequired) return null;
    const free = Number(material.current_qty) - Number(material.allocated_qty);
    return Math.floor(free / qtyRequired);
  };

  const bottleneckIndex = (() => {
    const values = lines.map(maxTheoretical);
    const real = values.filter((v): v is number => v !== null);
    if (real.length < 2) return -1;
    const min = Math.min(...real);
    return values.indexOf(min);
  })();

  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-slate-500">
        Packaging (boxes, labels, packing materials) required to pack and ship one unit — reserved when an order
        allocates, consumed only when it ships. Never consumed by recording a build.
      </p>
      {materials && materials.length > 8 && (
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
            <th className="p-2">Qty required</th>
            <th className="p-2">Max theoretical</th>
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
                  value={line.qty_required}
                  onChange={(e) => updateLine(i, { qty_required: e.target.value })}
                />
              </td>
              <td className={`p-2 ${i === bottleneckIndex ? "font-semibold text-amber-700" : "text-slate-500"}`}>
                {maxTheoretical(line) ?? "—"}
                {i === bottleneckIndex && <span className="ml-1 text-xs">(bottleneck)</span>}
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
      <div className="flex gap-2">
        <button onClick={addLine} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add material
        </button>
        <button onClick={() => saveMutation.mutate()} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
          Save kitting BOM
        </button>
        <SaveIndicator status={saveStatus} />
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
