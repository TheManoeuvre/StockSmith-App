import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { Material } from "../../api/types";
import { MaterialSelect } from "../materials/MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";
import { SaveIndicator } from "../common/SaveIndicator";
import { useSaveStatus } from "../../hooks/useSaveStatus";

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
  baseBom,
  effectiveBom,
  materials,
  onSave,
  onSaved,
}: {
  title: string;
  seedKey: number;
  baseBom: OverrideableLine[];
  effectiveBom: EffectiveLine[];
  materials: Material[];
  onSave: (payload: EffectiveLine[]) => Promise<unknown>;
  onSaved: () => void;
}) {
  const [overrides, setOverrides] = useState<Record<number, OverrideRow>>({});
  const [additiveLines, setAdditiveLines] = useState<EffectiveLine[]>([]);
  const seededKey = useRef<number | null>(null);

  useEffect(() => {
    // Seed local edit state once per seedKey (the variant id), not on every background
    // refetch — otherwise a refetch while the user is mid-edit silently discards their
    // unsaved changes before they get a chance to save.
    if (seededKey.current === seedKey) return;
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
    setOverrides(next);

    const baseIds = new Set(baseBom.map((b) => b.material_id));
    setAdditiveLines(effectiveBom.filter((l) => l.replaces_material_id == null && !baseIds.has(l.material_id)));
    seededKey.current = seedKey;
  }, [effectiveBom, baseBom, seedKey]);

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
    onSuccess: onSaved,
  });

  const setMode = (materialId: number, mode: OverrideMode) => {
    setOverrides((prev) => {
      const base = baseBom.find((b) => b.material_id === materialId);
      const existing = prev[materialId];
      const defaultQty = existing?.qty_required || base?.qty_required || "0";
      if (mode === "substitute") {
        const baseMaterial = materials.find((m) => m.id === materialId);
        const firstOther = materials.find((m) => m.id !== materialId && m.category === baseMaterial?.category);
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
                <td className="p-1">
                  {material?.name ?? base.material_id}
                  {o.mode === "substitute" && substituteMaterial && (
                    <div className="text-xs text-slate-500">
                      {substituteMaterial.name} (replaces {material?.name ?? base.material_id})
                    </div>
                  )}
                </td>
                <td className="p-1 text-slate-400">{base.qty_required}</td>
                <td className="p-1">
                  <select
                    className="rounded border border-slate-300 px-1 py-1 text-sm"
                    value={o.mode}
                    onChange={(e) => setMode(base.material_id, e.target.value as OverrideMode)}
                  >
                    <option value="inherit">Inherit</option>
                    <option value="qty">Override qty</option>
                    <option value="substitute">Substitute material</option>
                  </select>
                </td>
                <td className="p-1">
                  {o.mode === "qty" && (
                    <input
                      className="w-24 rounded border border-slate-300 px-2 py-1"
                      placeholder={base.qty_required}
                      value={o.qty_required}
                      onChange={(e) => updateQty(base.material_id, e.target.value)}
                    />
                  )}
                  {o.mode === "substitute" && (
                    <div className="flex items-center gap-1">
                      <MaterialSelect
                        materials={materials.filter((m) => m.id !== base.material_id && m.category === material?.category)}
                        value={o.substitute_material_id ?? base.material_id}
                        onChange={(id) => updateSubstituteMaterial(base.material_id, id)}
                      />
                      <input
                        className="w-20 rounded border border-slate-300 px-2 py-1"
                        value={o.qty_required}
                        onChange={(e) => updateQty(base.material_id, e.target.value)}
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
      <button onClick={() => saveMutation.mutate()} className="mt-2 rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
        Save {title.toLowerCase()}
      </button>
      <SaveIndicator status={saveStatus} />
      <ErrorBanner error={saveMutation.error} />
    </div>
  );
}
