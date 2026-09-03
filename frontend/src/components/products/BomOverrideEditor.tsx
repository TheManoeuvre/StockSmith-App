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
import { formatMoney } from "../../lib/money";

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
  kittingOnly = false,
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
  /** Passed to MaterialSelect for the swap and extra-material pickers — the kitting-BOM
   *  override table sets it to hide categories not flagged "Show in Kitting BOM list". */
  kittingOnly?: boolean;
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
        // A retired material is never a useful default replacement — skip inactive ones.
        const firstOther = materials.find(
          (m) => m.id !== materialId && m.is_active && sameCategory(m, baseMaterial)
        );
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

  const setAdditive = (updater: (prev: EffectiveLine[]) => EffectiveLine[]) =>
    setValue((prev) => ({ ...prev, additiveLines: updater(prev.additiveLines) }));
  const addAdditiveLine = () => {
    const first = materials[0];
    if (!first) return;
    setAdditive((prev) => [
      ...prev,
      { material_id: first.id, qty_required: "1", replaces_material_id: null },
    ]);
  };

  const overrideFor = (materialId: number): OverrideRow =>
    overrides[materialId] ?? { mode: "inherit", qty_required: "", substitute_material_id: null };

  /** The material and quantity a line actually resolves to, after its override. */
  const resolveLine = (base: OverrideableLine) => {
    const o = overrideFor(base.material_id);
    const mat =
      o.mode === "substitute" && o.substitute_material_id != null
        ? materials.find((m) => m.id === o.substitute_material_id)
        : materials.find((m) => m.id === base.material_id);
    const qty = o.mode === "inherit" ? base.qty_required : o.qty_required;
    return { mat, qty, inherited: o.mode === "inherit" };
  };
  const effectiveLabel = (mat: Material | undefined, qty: string): string => {
    if (!mat) return "—";
    const n = Number(qty);
    const unit = mat.unit === "each" ? "" : ` ${mat.unit}`;
    const cost = Number.isFinite(n) ? formatMoney(String(n * Number(mat.avg_unit_cost)), "GBP") : "—";
    return `${qty}${unit} · ${cost}`;
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

  // The tightest line — the one holding buildable count down — named for the footer note.
  const overallBottleneck = (() => {
    let worst: { qty: number | null; name: string } | null = null;
    for (const base of baseBom) {
      const cover = bottleneckFor(base, overrideFor(base.material_id))?.line_max_buildable ?? null;
      const { mat } = resolveLine(base);
      if (worst == null || (cover != null && (worst.qty == null || cover < worst.qty))) {
        worst = { qty: cover, name: mat?.name ?? String(base.material_id) };
      }
    }
    return worst;
  })();

  const MODES: { mode: OverrideMode; label: string }[] = [
    { mode: "inherit", label: "Inherit" },
    { mode: "qty", label: "Qty" },
    { mode: "substitute", label: "Swap" },
  ];

  return (
    <div className="mt-3">
      <h4 className="mb-1 text-sm font-medium text-slate-600">{title}</h4>
      <div className="overflow-hidden rounded border border-slate-200">
        {baseBom.map((base) => {
          const material = materials.find((m) => m.id === base.material_id);
          const o = overrideFor(base.material_id);
          const swap = o.mode === "substitute";
          const { mat: effMat, qty: effQty, inherited } = resolveLine(base);
          return (
            <div
              key={base.material_id}
              className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-1.5 last:border-0"
            >
              <span
                className={`shrink-0 truncate text-xs text-slate-500 ${swap ? "w-24" : "w-40"}`}
                title={material?.name}
              >
                {material?.name ?? base.material_id}
              </span>

              <div className="flex shrink-0 gap-px rounded bg-slate-100 p-0.5">
                {MODES.map(({ mode, label }) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setMode(base.material_id, mode)}
                    className={`rounded px-2 py-0.5 text-[11px] font-semibold ${
                      o.mode === mode
                        ? "bg-white text-slate-900 shadow-sm"
                        : "text-slate-500 hover:text-slate-700"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {swap && (
                <MaterialSelect
                  materials={materials.filter(
                    (m) =>
                      m.id !== base.material_id &&
                      sameCategory(m, material) &&
                      // Hide retired materials, but keep one that's already the saved
                      // substitute so the row doesn't silently re-point itself.
                      (m.is_active || m.id === o.substitute_material_id)
                  )}
                  value={o.substitute_material_id ?? base.material_id}
                  onChange={(id) => updateSubstituteMaterial(base.material_id, id)}
                  kittingOnly={kittingOnly}
                  className="h-[26px] min-w-0 flex-1 rounded border border-slate-300 px-1.5 text-xs"
                />
              )}

              {o.mode !== "inherit" && (
                <input
                  className="w-16 shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-right text-xs tabular-nums"
                  step={wholeNumberStepFor(effMat?.unit)}
                  placeholder={base.qty_required}
                  value={o.qty_required}
                  onChange={(e) => updateQty(base.material_id, e.target.value)}
                  onBlur={(e) =>
                    updateQty(base.material_id, normalizeQtyForUnit(e.target.value, effMat?.unit))
                  }
                />
              )}

              {!swap && <div className="min-w-0 flex-1" />}

              <span
                className={`shrink-0 text-right text-[11px] tabular-nums ${
                  swap ? "w-20" : "w-28"
                } ${inherited ? "text-slate-400" : "text-slate-700"}`}
              >
                {effectiveLabel(effMat, effQty)}
              </span>
            </div>
          );
        })}

        {additiveLines.map((line, i) => {
          const mat = materials.find((m) => m.id === line.material_id);
          const n = Number(line.qty_required);
          return (
            <div
              key={`add-${i}`}
              className="flex items-center gap-2 border-b border-slate-100 bg-slate-50 px-2.5 py-1.5 last:border-0"
            >
              <span className="w-20 shrink-0 text-[11px] font-semibold text-teal-700">
                extra
              </span>
              <MaterialSelect
                materials={materials}
                value={line.material_id}
                onChange={(id) =>
                  setAdditive((prev) =>
                    prev.map((l, j) => (j === i ? { ...l, material_id: id } : l)),
                  )
                }
                kittingOnly={kittingOnly}
                className="h-[26px] min-w-0 flex-1 rounded border border-slate-300 px-1.5 text-xs"
              />
              <input
                className="w-16 shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-right text-xs tabular-nums"
                step={wholeNumberStepFor(mat?.unit)}
                value={line.qty_required}
                onChange={(e) =>
                  setAdditive((prev) =>
                    prev.map((l, j) => (j === i ? { ...l, qty_required: e.target.value } : l)),
                  )
                }
                onBlur={(e) =>
                  setAdditive((prev) =>
                    prev.map((l, j) =>
                      j === i
                        ? { ...l, qty_required: normalizeQtyForUnit(e.target.value, mat?.unit) }
                        : l,
                    ),
                  )
                }
              />
              <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-slate-500">
                {mat && Number.isFinite(n)
                  ? formatMoney(String(n * Number(mat.avg_unit_cost)), "GBP")
                  : "—"}
              </span>
              <button
                type="button"
                onClick={() => setAdditive((prev) => prev.filter((_, j) => j !== i))}
                aria-label="Remove extra line"
                className="shrink-0 text-red-600 hover:text-red-700"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      <div className="mt-1.5 flex items-center gap-2">
        <button
          type="button"
          onClick={addAdditiveLine}
          className="rounded border border-dashed border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:border-blue-500 hover:text-blue-600"
        >
          + Extra material
        </button>
        <span className="text-[11px] text-slate-400">
          {overallBottleneck
            ? overallBottleneck.qty == null
              ? "No materials"
              : `Limited by ${overallBottleneck.name}`
            : "No materials"}
        </span>
      </div>

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
