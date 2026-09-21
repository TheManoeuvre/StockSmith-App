import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { materialSubstitutesApi } from "../../api/materialSubstitutes";
import { materialsApi } from "../../api/materials";
import type { MaterialSubstitute } from "../../api/types";
import { MaterialSelect } from "./MaterialSelect";
import { ErrorBanner } from "../common/ErrorBanner";

/**
 * Curates a material's ranked, human-picked fallbacks — e.g. a bigger box as a fallback for
 * a smaller one that's run out, or a close-proxy filament colour. Nothing here ever
 * substitutes automatically: it feeds the options offered wherever a shortage is detected —
 * the build form (which draws from the chosen fallback after confirmation) and pack-time
 * kitting shortfalls on the dashboard (see SubstituteSuggestions).
 *
 * There's no delete endpoint by design (keeps history) — "removing" a fallback deactivates
 * it (PATCH is_active=false) rather than dropping the row, and reorder is done by swapping
 * two rows' rank via PATCH rather than a dedicated reorder endpoint.
 */
export function MaterialSubstitutesSection({ materialId }: { materialId: number }) {
  const queryClient = useQueryClient();
  const { data: materials } = useQuery({ queryKey: ["materials"], queryFn: materialsApi.list });
  const { data: substitutes } = useQuery({
    queryKey: ["materials", materialId, "substitutes"],
    queryFn: () => materialSubstitutesApi.list(materialId),
  });

  const [showInactive, setShowInactive] = useState(false);
  const [newSubstituteId, setNewSubstituteId] = useState<number | null>(null);
  const [newNotes, setNewNotes] = useState("");

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["materials", materialId, "substitutes"] });

  const reorderMutation = useMutation({
    mutationFn: ([a, b]: [MaterialSubstitute, MaterialSubstitute]) =>
      Promise.all([
        materialSubstitutesApi.update(materialId, a.id, { rank: b.rank }),
        materialSubstitutesApi.update(materialId, b.id, { rank: a.rank }),
      ]),
    onSuccess: invalidate,
  });

  const toggleActiveMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) =>
      materialSubstitutesApi.update(materialId, id, { is_active }),
    onSuccess: invalidate,
  });

  const addMutation = useMutation({
    mutationFn: () => {
      const substituteId = newSubstituteId ?? pickable[0]?.id ?? null;
      if (substituteId == null) return Promise.reject(new Error("Pick a material first"));
      const nextRank = (active.length > 0 ? active[active.length - 1].rank : -1) + 1;
      return materialSubstitutesApi.add(materialId, {
        substitute_material_id: substituteId,
        rank: nextRank,
        notes: newNotes.trim(),
      });
    },
    onSuccess: () => {
      invalidate();
      setNewSubstituteId(null);
      setNewNotes("");
    },
  });

  const all = substitutes ?? [];
  const active = [...all.filter((s) => s.is_active)].sort((a, b) => a.rank - b.rank || a.id - b.id);
  const inactive = all.filter((s) => !s.is_active);

  // A material can't fall back to itself, and offering an already-active fallback again would
  // just hit the backend's duplicate-substitute 400 — so both are excluded from the picker.
  // The picker is also scoped to the material's own category: a filament falling back to a
  // box is never a sensible suggestion, and the full list is long enough that the wrong
  // categories drown out the real candidates. Until the materials list has loaded the
  // category is unknown, so nothing is offered rather than briefly offering everything.
  const excludedIds = new Set([materialId, ...active.map((s) => s.substitute_material_id)]);
  const category = materials?.find((m) => m.id === materialId)?.category;
  const pickable = (materials ?? []).filter(
    (m) => m.is_active && !excludedIds.has(m.id) && category != null && m.category === category
  );

  const effectiveNewSubstituteId = newSubstituteId ?? pickable[0]?.id ?? null;
  const canAdd = effectiveNewSubstituteId != null && newNotes.trim() !== "" && !addMutation.isPending;

  return (
    <div className="mt-3">
      <h4 className="mb-1 text-sm font-medium text-slate-600">Fallback materials</h4>
      <p className="mb-2 text-xs text-slate-400">
        Ranked, human-picked alternatives for when this material runs out. Suggested only —
        nothing here is ever substituted automatically.
      </p>

      <div className="overflow-hidden rounded border border-slate-200">
        {active.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-slate-400">No fallbacks set.</p>
        ) : (
          active.map((sub, i) => (
            <div
              key={sub.id}
              className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-1.5 last:border-0"
            >
              <span className="w-5 shrink-0 text-[11px] font-semibold text-slate-400 tabular-nums">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium text-slate-700">
                  {sub.substitute_material_name ?? sub.substitute_material_id}
                </p>
                {sub.notes && <p className="truncate text-[11px] text-slate-500">{sub.notes}</p>}
              </div>
              <div className="flex shrink-0 items-center gap-0.5">
                <button
                  type="button"
                  aria-label="Move up"
                  disabled={i === 0 || reorderMutation.isPending}
                  onClick={() => reorderMutation.mutate([sub, active[i - 1]])}
                  className="rounded px-1 py-0.5 text-xs text-slate-500 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  ▲
                </button>
                <button
                  type="button"
                  aria-label="Move down"
                  disabled={i === active.length - 1 || reorderMutation.isPending}
                  onClick={() => reorderMutation.mutate([sub, active[i + 1]])}
                  className="rounded px-1 py-0.5 text-xs text-slate-500 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-30"
                >
                  ▼
                </button>
              </div>
              <button
                type="button"
                disabled={toggleActiveMutation.isPending}
                onClick={() => toggleActiveMutation.mutate({ id: sub.id, is_active: false })}
                className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-red-600 hover:text-red-700 disabled:opacity-50"
              >
                Deactivate
              </button>
            </div>
          ))
        )}
      </div>

      {inactive.length > 0 && (
        <button
          type="button"
          onClick={() => setShowInactive((v) => !v)}
          className="mt-1.5 text-[11px] font-semibold text-slate-500 hover:text-slate-700"
        >
          {showInactive ? "Hide" : "Show"} {inactive.length} inactive
        </button>
      )}
      {showInactive && inactive.length > 0 && (
        <div className="mt-1.5 overflow-hidden rounded border border-slate-200 bg-slate-50">
          {inactive.map((sub) => (
            <div
              key={sub.id}
              className="flex items-center gap-2 border-b border-slate-100 px-2.5 py-1.5 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs text-slate-500">
                  {sub.substitute_material_name ?? sub.substitute_material_id}
                </p>
                {sub.notes && <p className="truncate text-[11px] text-slate-400">{sub.notes}</p>}
              </div>
              <button
                type="button"
                disabled={toggleActiveMutation.isPending}
                onClick={() => toggleActiveMutation.mutate({ id: sub.id, is_active: true })}
                className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold text-slate-600 hover:text-slate-800 disabled:opacity-50"
              >
                Reactivate
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <MaterialSelect
          materials={pickable}
          value={effectiveNewSubstituteId ?? 0}
          onChange={setNewSubstituteId}
          disabled={pickable.length === 0}
          className="h-[26px] min-w-0 flex-1 rounded border border-slate-300 px-1.5 text-xs"
        />
        <input
          className="h-[26px] min-w-0 flex-[2] rounded border border-slate-300 px-1.5 text-xs"
          placeholder="Why this is a fallback (required)…"
          value={newNotes}
          onChange={(e) => setNewNotes(e.target.value)}
        />
        <button
          type="button"
          disabled={!canAdd}
          onClick={() => addMutation.mutate()}
          className="shrink-0 rounded border border-dashed border-slate-300 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:border-blue-500 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Add fallback
        </button>
      </div>
      <ErrorBanner
        error={reorderMutation.error ?? toggleActiveMutation.error ?? addMutation.error}
      />
    </div>
  );
}
