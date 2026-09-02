import { useMutation } from "@tanstack/react-query";
import { useMemo } from "react";
import type { Material } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveButton } from "../common/SaveButton";
import { useSaveStatus } from "../../hooks/useSaveStatus";
import { useEditableCopy } from "../../hooks/useEditableCopy";
import { useManagedSave } from "../../hooks/useDirtyRegistry";
import { normalizeQtyForUnit, wholeNumberStepFor } from "../../lib/format";

/**
 * Whether two materials are in the same category, which is the rule the backend enforces on
 * substitutions (routers/variants.py).
 *
 * Compares ids where both have one — names are user-editable now, so the id is the identity —
 * and falls back to the name for a material written before categories became rows.
 */
function sameCategory(a: Material | undefined, b: Material | undefined): boolean {
  if (!a || !b) return false;
  if (a.category_id !== null && b.category_id !== null) return a.category_id === b.category_id;
  return a.category === b.category;
}

type OverrideMode = "inherit" | "qty" | "substitute";
interface OverrideRow {
  mode: OverrideMode;
  qty_required: string;
  substitute_material_id: number | null;
}

interface OverrideableLine {
  material_id: number;
  qty_required: string;
}

interface EffectiveLine extends OverrideableLine {
  replaces_material_id: number | null;
  line_max_buildable?: number | null;
  line_expected_max_buildable?: number | null;
}

// Shared by VariantEditor for both the build BOM and kitting BOM override tables — same
// qty-override/substitution/additive editing UI and save flow, just pointed at different
// base/effective BOMs and a different save endpoint.
export function BomOverrideEditor({
  title,
  seedKey,
  dirtyKey,
  baseBom,
  effectiveBom,
  materials,
  onSave,
  onSaved,
}: {
  title: string;
  seedKey: number;
  /**
   * Registry key within the enclosing <DirtyPath> — both instances in a variant row share
   * seedKey (the variant id), so they need distinct keys to avoid colliding in the registry.
   */
  dirtyKey: string;
  baseBom: OverrideableLine[];
  effectiveBom: EffectiveLine[];
  materials: Material[];
  onSave: (payload: EffectiveLine[]) => Promise<unknown>;
  onSaved: () => void;
}) {
  // Derived, not stored: useEditableCopy takes it from here once per seedKey (the variant
  // id) and then ignores later changes, which is what stops a background refetch discarding
  // an in-progress edit.
  const seed = useMemo(() => {
    const next: Record<number, OverrideRow> = {};
    for (const base of baseBom) {
      const subLine = effectiveBom.find((l) => l.replaces_material_id === base.material_id);
      if (subLine) {
        next[base.material_id] = {
          mode: "substitute",
          qty_required: subLine.qty_required,
          substitute_material_id: subLine.material_id,
        };
        continue;
      }
      const qtyLine = effectiveBom.find((l) => l.material_id === base.material_id && l.replaces_material_id == null);
      if (qtyLine && qtyLine.qty_required !== base.qty_required) {
        next[base.material_id] = { mode: "qty", qty_required: qtyLine.qty_required, substitute_material_id: null };
        continue;
      }
      next[base.material_id] = { mode: "inherit", qty_required: "", substitute_material_id: null };
    }
    const baseIds = new Set(baseBom.map((b) => b.material_id));
    return {
      overrides: next,
      additiveLines: effectiveBom.filter((l) => l.replaces_material_id == null && !baseIds.has(l.material_id)),
    };
  }, [baseBom, effectiveBom]);

  const { value, setValue, isDirty, markSaved, revert } = useEditableCopy<{
    overrides: Record<number, OverrideRow>;
    additiveLines: EffectiveLine[];
  }>({
    key: dirtyKey,
    label: title,
    initial: { overrides: {}, additiveLines: [] },
    seed,
    seedKey,
  });
  const { overrides, additiveLines } = value;
  const setOverrides = (updater: (prev: Record<number, OverrideRow>) => Record<number, OverrideRow>) =>
    setValue((prev) => ({ ...prev, overrides: updater(prev.overrides) }));

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: EffectiveLine[] = [
        ...baseBom.flatMap((base): EffectiveLine[] => {
          const o = overrides[base.material_id];
          if (!o || o.mode === "inherit") return [];
          if (o.mode === "qty") {
            return [{ material_id: base.material_id, qty_required: o.qty_required, replaces_material_id: null }];
          }
          return o.substitute_material_id != null
            ? [
                {
                  material_id: o.substitute_material_id,
                  qty_required: o.qty_required,
                  replaces_material_id: base.material_id,
                },
              ]
            : [];
        }),
        ...additiveLines,
      ];
      return onSave(payload);
    },
    onSuccess: () => {
      // The endpoint returns stored override rows, not this editor's derived
      // overrides/additiveLines shape, so the baseline is what we sent. Safe here: the values
      // are echoed back unchanged, and a later refetch can no longer re-seed anyway.
      markSaved();
      onSaved();
    },
  });

  const setMode = (materialId: number, mode: OverrideMode) => {
    setOverrides((prev) => {
      const base = baseBom.find((b) => b.material_id === materialId);
      const existing = prev[materialId];
      const defaultQty = existing?.qty_required || base?.qty_required || "0";
      if (mode === "substitute") {
        const baseMaterial = materials.find((m) => m.id === materialId);
        const firstOther = materials.find((m) => m.id !== materialId && sameCategory(m, baseMaterial));
        return {
          ...prev,
          [materialId]: {
            mode,
            qty_required: defaultQty,
            substitute_material_id: existing?.substitute_material_id ?? firstOther?.id ?? null,
          },
        };
      }
      return { ...prev, [materialId]: { mode, qty_required: defaultQty, substitute_material_id: null } };
    });
  };

  const updateQty = (materialId: number, qty: string) => {
    setOverrides((prev) => ({ ...prev, [materialId]: { ...prev[materialId], qty_required: qty } }));
  };

  const updateSubstituteMaterial = (materialId: number, subMaterialId: number) => {
    setOverrides((prev) => ({ ...prev, [materialId]: { ...prev[materialId], substitute_material_id: subMaterialId } }));
  };

  const bottleneckFor = (base: OverrideableLine, o: OverrideRow): EffectiveLine | undefined => {
    if (o.mode === "substitute" && o.substitute_material_id != null) {
      return effectiveBom.find(
        (l) => l.material_id === o.substitute_material_id && l.replaces_material_id === base.material_id
      );
    }
    return effectiveBom.find((l) => l.material_id === base.material_id && l.replaces_material_id == null);
  };

  const saveStatus = useSaveStatus(saveMutation.status);
  const managed = useManagedSave(dirtyKey, {
    save: () => saveMutation.mutate(),
    revert,
  });

  if (baseBom.length === 0) {
    return (
      <div className="mt-3">
        <h4 className="mb-1 text-sm font-medium text-slate-600">{title}</h4>
        <p className="text-xs text-slate-400">No lines set on the base product.</p>
      </div>
    );
  }

  return (
    <div className="mt-3">
      <h4 className="mb-1 text-sm font-medium text-slate-600">{title}</h4>
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            <th className="p-1">Material</th>
            <th className="p-1">Base qty</th>
            <th className="p-1">Mode</th>
            <th className="p-1">Value</th>
            <th className="p-1">Max theoretical</th>
          </tr>
        </thead>
        <tbody>
          {baseBom.map((base) => {
            const material = materials.find((m) => m.id === base.material_id);
            const o: OverrideRow = overrides[base.material_id] ?? {
              mode: "inherit",
              qty_required: "",
              substitute_material_id: null,
            };
            const substituteMaterial =
              o.substitute_material_id != null ? materials.find((m) => m.id === o.substitute_material_id) : undefined;
            const bottleneck = bottleneckFor(base, o);
            return (
              <tr key={base.material_id}>
                <td className="p-1">{material?.name ?? base.material_id}</td>
                <td className="p-1 text-slate-400">{base.qty_required}</td>
                <td className="p-1">
                  <select
                    className="rounded border border-slate-300 px-1 py-1 text-sm"
                    value={o.mode}
                    onChange={(e) => setMode(base.material_id, e.target.value as OverrideMode)}
                  >
                    <option value="inherit">Inherit</option>
                    <option value="qty">Override qty</option>
                    <option value="substitute">Substitute</option>
                  </select>
                </td>
                <td className="p-1">
                  {o.mode === "qty" && (
                    <input
                      className="w-24 rounded border border-slate-300 px-2 py-1"
                      step={wholeNumberStepFor(material?.unit)}
                      placeholder={base.qty_required}
                      value={o.qty_required}
                      onChange={(e) => updateQty(base.material_id, e.target.value)}
                      onBlur={(e) => updateQty(base.material_id, normalizeQtyForUnit(e.target.value, material?.unit))}
                    />
                  )}
                  {o.mode === "substitute" && (
                    <div className="flex flex-col gap-1">
                      <input
                        className="w-20 rounded border border-slate-300 px-2 py-1"
                        step={wholeNumberStepFor(substituteMaterial?.unit)}
                        value={o.qty_required}
                        onChange={(e) => updateQty(base.material_id, e.target.value)}
                        onBlur={(e) => updateQty(base.material_id, normalizeQtyForUnit(e.target.value, substituteMaterial?.unit))}
                      />
                      <MaterialSelect
                        materials={materials.filter((m) => m.id !== base.material_id && sameCategory(m, material))}
                        value={o.substitute_material_id ?? base.material_id}
                        onChange={(id) => updateSubstituteMaterial(base.material_id, id)}
                        className="w-full rounded border border-slate-300 px-2 py-1"
                      />
                    </div>
                  )}
                </td>
                <td className={`p-1 ${bottleneck?.line_max_buildable != null ? "text-slate-600" : "text-slate-400"}`}>
                  {bottleneck?.line_max_buildable ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!managed && (
        <SaveButton
          isDirty={isDirty}
          isPending={saveMutation.isPending}
          status={saveStatus}
          onClick={() => saveMutation.mutate()}
          className="mt-2 rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          Save {title.toLowerCase()}
        </SaveButton>
      )}
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
