import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { materialsApi } from "../../api/materials";
import { ordersApi } from "../../api/orders";
import type { OrderKittingOverrideLine } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useManagedSave } from "../../hooks/useDirtyRegistry";
import { formatMoney } from "../../lib/money";

interface KittingEdits {
  /** qty override per auto-computed material line, keyed by material_id. "" = inherit. */
  overrides: Record<number, string>;
  /** Extra lines added on top of the auto set. */
  extras: OrderKittingOverrideLine[];
}

// Lets an order override its auto-computed aggregate kitting requirement — e.g. two lines
// that each nominally need a label only need one shared label for the order as a whole.
// Overrides here don't change per-unit BOM rates like the product/variant kitting BOM
// editors; qty_required here is an absolute total for the order.
//
// The Cost column is what this order's packaging costs, and it's why the override matters
// beyond stock: it feeds Kitting COGS in the panel above. Two bases are shown — Cost is
// forward-looking (ordered basis, moves as soon as an override is saved) while the consumed
// total is realised (shipped basis, what Kitting COGS actually reports). They converge once
// the order has fully shipped.
export function OrderKittingSection({ orderId, currency }: { orderId: number; currency: string | null }) {
  const queryClient = useQueryClient();
  const { data: summary } = useQuery({
    queryKey: ["orders", orderId, "kitting-overrides"],
    queryFn: () => ordersApi.getKittingOverrides(orderId),
  });
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });

  const seed = useMemo<KittingEdits | undefined>(() => {
    if (!summary) return undefined;
    const autoMaterialIds = new Set(summary.lines.map((l) => l.material_id));
    const overrides: Record<number, string> = {};
    const extras: OrderKittingOverrideLine[] = [];
    for (const o of summary.overrides) {
      if (o.replaces_material_id == null && autoMaterialIds.has(o.material_id)) {
        overrides[o.material_id] = o.qty_required;
      } else {
        extras.push(o);
      }
    }
    return { overrides, extras };
  }, [summary]);

  const { value, setValue, isDirty, markSaved, revert } = useEditableCopy<KittingEdits>({
    key: "order-kitting",
    label: "Kitting overrides",
    initial: { overrides: {}, extras: [] },
    seed,
    seedKey: orderId,
  });
  const { overrides, extras } = value;
  const setOverride = (materialId: number, qty: string) =>
    setValue((prev) => ({ ...prev, overrides: { ...prev.overrides, [materialId]: qty } }));
  const setExtras = (updater: (prev: OrderKittingOverrideLine[]) => OrderKittingOverrideLine[]) =>
    setValue((prev) => ({ ...prev, extras: updater(prev.extras) }));

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
        ...extras,
      ];
      return ordersApi.replaceKittingOverrides(orderId, payload);
    },
    onSuccess: () => {
      markSaved();
      invalidate();
    },
  });

  const saveStatus = useSaveStatus(saveMutation.status);
  const managed = useManagedSave("order-kitting", {
    save: () => saveMutation.mutate(),
    revert,
  });

  const addExtraOverride = () => {
    const usedIds = new Set([
      ...Object.keys(overrides).map(Number),
      ...extras.map((o) => o.material_id),
    ]);
    const firstUnused = materials?.find((m) => !usedIds.has(m.id));
    if (!firstUnused) return;
    setExtras((prev) => [
      ...prev,
      { material_id: firstUnused.id, qty_required: "1", replaces_material_id: null },
    ]);
  };

  if (!summary || (summary.lines.length === 0 && extras.length === 0)) {
    return null;
  }

  return (
    <div className="rounded bg-white p-4 shadow-sm">
      <h2 className="mb-1 text-lg font-semibold">Kitting</h2>
      <p className="mb-2 text-sm text-slate-500">
        Auto-computed from each line's kitting BOM. Override the qty for a material shared across lines (e.g. one
        label instead of two) — the cost follows the override, and feeds Kitting COGS above.
      </p>
      <table className="w-full border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="p-2">Material</th>
            <th className="p-2">Auto qty</th>
            <th className="p-2">Override</th>
            <th className="p-2">Effective</th>
            <th className="p-2">Cost</th>
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
                  onChange={(e) => setOverride(line.material_id, e.target.value)}
                />
              </td>
              <td className="p-2">{line.effective_qty}</td>
              <td
                className="p-2"
                title={
                  line.unit_cost_is_frozen
                    ? `${line.unit_cost} each — frozen when this material was consumed`
                    : `${line.unit_cost} each — today's material cost, frozen once consumed`
                }
              >
                {formatMoney(line.effective_cost, currency)}
              </td>
              <td className="p-2">{line.reserved_qty}</td>
              <td className="p-2">
                {line.consumed_qty}
                {Number(line.consumed_qty) > 0 && (
                  <span className="block text-xs text-slate-500">{formatMoney(line.consumed_cost, currency)}</span>
                )}
              </td>
            </tr>
          ))}
          {extras.map((o, i) => {
            const material = materials?.find((m) => m.id === o.material_id);
            return (
              <tr key={`extra-${i}`} className="border-b border-slate-100 bg-slate-50">
                <td className="p-2">
                  <MaterialSelect
                    materials={materials ?? []}
                    value={o.material_id}
                    onChange={(material_id) =>
                      setExtras((prev) => prev.map((x, j) => (j === i ? { ...x, material_id } : x)))
                    }
                  />
                  {!material && <span className="text-xs text-slate-400">Additional line</span>}
                </td>
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2">
                  <input
                    className="w-20 rounded border border-slate-300 px-2 py-1"
                    value={o.qty_required}
                    onChange={(e) =>
                      setExtras((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, qty_required: e.target.value } : x)),
                      )
                    }
                  />
                </td>
                {/* Effective/Cost/Reserved stay blank until saved — these rows aren't in the
                    server's computed summary yet. */}
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2 text-slate-400">—</td>
                <td className="p-2">
                  <button
                    onClick={() => setExtras((prev) => prev.filter((_, j) => j !== i))}
                    className="text-xs text-red-600"
                  >
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-2 text-sm">
        <p className="font-medium">Kitting cost {formatMoney(summary.effective_cost_total, currency)}</p>
        {Number(summary.effective_cost_total) !== Number(summary.consumed_cost_total) && (
          <p className="text-xs text-slate-500">
            {formatMoney(summary.consumed_cost_total, currency)} consumed so far — that's the figure in Kitting COGS
            above; they match once the order has fully shipped.
          </p>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button onClick={addExtraOverride} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
          + Add material override
        </button>
        {!managed && (
          <>
            <button
              onClick={() => saveMutation.mutate()}
              disabled={!isDirty}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              Save kitting overrides
            </button>
            <SaveIndicator status={saveStatus} />
          </>
        )}
      </div>
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
