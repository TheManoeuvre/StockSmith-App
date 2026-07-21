import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { materialsApi } from "../../api/materials";
import { ordersApi } from "../../api/orders";
import type { OrderKittingOverrideLine } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";

// Lets a multi-line order override its auto-computed aggregate packaging requirement —
// e.g. two lines that each nominally need a label only need one shared label for the
// order as a whole. Overrides here don't change per-unit BOM rates like the product/
// variant kitting BOM editors; qty_required here is an absolute total for the order.
export function OrderKittingSection({ orderId }: { orderId: number }) {
  const queryClient = useQueryClient();
  const { data: summary } = useQuery({
    queryKey: ["orders", orderId, "kitting-overrides"],
    queryFn: () => ordersApi.getKittingOverrides(orderId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const [overrides, setOverrides] = useState<Record<number, string>>({});
  const [extraOverrides, setExtraOverrides] = useState<OrderKittingOverrideLine[]>([]);
  const seeded = useRef(false);

  useEffect(() => {
    if (!summary || seeded.current) return;
    const autoMaterialIds = new Set(summary.lines.map((l) => l.material_id));
    const next: Record<number, string> = {};
    const extras: OrderKittingOverrideLine[] = [];
    for (const o of summary.overrides) {
      if (o.replaces_material_id == null && autoMaterialIds.has(o.material_id)) {
        next[o.material_id] = o.qty_required;
      } else {
        extras.push(o);
      }
    }
    setOverrides(next);
    setExtraOverrides(extras);
    seeded.current = true;
  }, [summary]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["orders", orderId, "kitting-overrides"] });
    queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
    queryClient.invalidateQueries({ queryKey: ["materials"] });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: OrderKittingOverrideLine[] = [
        ...Object.entries(overrides)
          .filter(([, qty]) => qty.trim() !== "")
          .map(([materialId, qty]) => ({
            material_id: Number(materialId),
            qty_required: qty,
            replaces_material_id: null,
          })),
        ...extraOverrides,
      ];
      return ordersApi.replaceKittingOverrides(orderId, payload);
    },
    onSuccess: invalidate,
  });

  const addExtraOverride = () => {
    const usedIds = new Set([...Object.keys(overrides).map(Number), ...extraOverrides.map((o) => o.material_id)]);
    const firstUnused = materials?.find((m) => !usedIds.has(m.id));
    if (!firstUnused) return;
    setExtraOverrides((prev) => [...prev, { material_id: firstUnused.id, qty_required: "1", replaces_material_id: null }]);
  };

  const updateExtraOverride = (index: number, patch: Partial<OrderKittingOverrideLine>) => {
    setExtraOverrides((prev) => prev.map((o, i) => (i === index ? { ...o, ...patch } : o)));
  };

  const removeExtraOverride = (index: number) => setExtraOverrides((prev) => prev.filter((_, i) => i !== index));

  const saveStatus = useSaveStatus(saveMutation.status);

  if (!summary || (summary.lines.length === 0 && extraOverrides.length === 0)) {
    return null;
  }

  return (
    <div className="rounded bg-white p-4 shadow-sm">
      <h2 className="mb-1 text-lg font-semibold">Packaging</h2>
      <p className="mb-2 text-sm text-slate-500">
        Auto-computed from each line's kitting BOM. Override the qty for a material shared across lines (e.g. one
        label instead of two).
      </p>
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Material</th>
            <th className="p-2">Auto qty</th>
            <th className="p-2">Override</th>
            <th className="p-2">Reserved</th>
            <th className="p-2">Consumed</th>
          </tr>
        </thead>
        <tbody>
          {summary.lines.map((line) => (
            <tr key={line.material_id} className="border-b border-slate-100">
              <td className="p-2">{line.material_name}</td>
              <td className="p-2 text-slate-400">{line.auto_qty}</td>
              <td className="p-2">
                <input
                  className="w-20 rounded border border-slate-300 px-2 py-1"
                  placeholder={line.auto_qty}
                  value={overrides[line.material_id] ?? ""}
                  onChange={(e) => setOverrides((prev) => ({ ...prev, [line.material_id]: e.target.value }))}
                />
              </td>
              <td className="p-2">{line.reserved_qty}</td>
              <td className="p-2">{line.consumed_qty}</td>
            </tr>
          ))}
          {extraOverrides.map((o, i) => {
            const material = materials?.find((m) => m.id === o.material_id);
            return (
              <tr key={`extra-${i}`} className="border-b border-slate-100 bg-slate-50">
                <td className="p-2">
                  <MaterialSelect
                    materials={materials ?? []}
                    value={o.material_id}
                    onChange={(material_id) => updateExtraOverride(i, { material_id })}
                  />
                  {!material && <span className="text-xs text-slate-400">Additional line</span>}
                </td>
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2">
                  <input
                    className="w-20 rounded border border-slate-300 px-2 py-1"
                    value={o.qty_required}
                    onChange={(e) => updateExtraOverride(i, { qty_required: e.target.value })}
                  />
                </td>
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2">
                  <button onClick={() => removeExtraOverride(i)} className="text-xs text-red-600">
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-2 flex items-center gap-2">
        <button onClick={addExtraOverride} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add material override
        </button>
        <button onClick={() => saveMutation.mutate()} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
          Save packaging overrides
        </button>
        <SaveIndicator status={saveStatus} />
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
